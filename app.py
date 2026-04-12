import base64
import json
import os
from datetime import datetime
from functools import wraps
from urllib.parse import quote

import pytz
from azure.cosmos import CosmosClient
from dotenv import load_dotenv
from flask import Flask, abort, g, jsonify, redirect, render_template, request, url_for

load_dotenv()

app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET_KEY", "mono-tasks-dev-key")

israel_tz = pytz.timezone("Asia/Jerusalem")

COSMOS_DB_URI_ENV = "COSMOS_DB_URI"
COSMOS_DB_KEY_ENV = "COSMOS_DB_KEY"
DATABASE_NAME = os.getenv("COSMOS_DB_DATABASE", "ticketingdb")
CONTAINER_NAME = os.getenv("COSMOS_DB_CONTAINER", "ticketingdbcont")
AUTH_REQUIRED = os.getenv("AUTH_REQUIRED", "false").lower() == "true"
ALLOWED_TENANT_ID = os.getenv("ALLOWED_TENANT_ID")
REQUIRED_APP_ROLE = os.getenv("REQUIRED_APP_ROLE", "TaskUser")
DEV_AUTH_BYPASS = os.getenv("DEV_AUTH_BYPASS", "false").lower() == "true"

container = None


def get_required_env(name):
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


def get_container():
    global container
    if container is None:
        client = CosmosClient(
            get_required_env(COSMOS_DB_URI_ENV),
            credential=get_required_env(COSMOS_DB_KEY_ENV),
        )
        database = client.get_database_client(DATABASE_NAME)
        container = database.get_container_client(CONTAINER_NAME)
    return container


def now_date():
    return datetime.now(israel_tz).strftime("%d/%m/%Y")


def load_tickets():
    return list(get_container().read_all_items())


def next_ticket_id():
    tickets = load_tickets()
    return max([int(t["id"]) for t in tickets], default=0) + 1


def find_ticket(ticket_id):
    query = "SELECT * FROM c WHERE c.id = @ticket_id"
    params = [{"name": "@ticket_id", "value": ticket_id}]
    tickets = list(
        get_container().query_items(
            query=query,
            parameters=params,
            enable_cross_partition_query=True,
        )
    )
    if not tickets:
        raise LookupError(f"Ticket with ID {ticket_id} not found.")
    return tickets[0]


def parse_client_principal():
    raw_principal = request.headers.get("X-MS-CLIENT-PRINCIPAL")
    if not raw_principal:
        if not DEV_AUTH_BYPASS:
            return None
        return {
            "name": "Local Developer",
            "email": "developer@mono.local",
            "tenant_id": ALLOWED_TENANT_ID or "local-tenant",
            "roles": [REQUIRED_APP_ROLE],
            "source": "bypass",
        }

    decoded = base64.b64decode(raw_principal)
    principal = json.loads(decoded)
    claim_map = {}
    for claim in principal.get("claims", []):
        claim_map.setdefault(claim.get("typ"), []).append(claim.get("val"))

    def first(*keys):
        for key in keys:
            values = claim_map.get(key)
            if values:
                return values[0]
        return None

    roles = claim_map.get("roles", []) + claim_map.get("role", [])
    return {
        "name": first("name", "preferred_username", "http://schemas.xmlsoap.org/ws/2005/05/identity/claims/name"),
        "email": first(
            "preferred_username",
            "emails",
            "http://schemas.xmlsoap.org/ws/2005/05/identity/claims/emailaddress",
        ),
        "tenant_id": first("tid", "http://schemas.microsoft.com/identity/claims/tenantid"),
        "object_id": first("oid", "http://schemas.microsoft.com/identity/claims/objectidentifier"),
        "roles": roles,
        "source": "easy-auth",
    }


def has_required_access(user):
    if not user:
        return False
    if ALLOWED_TENANT_ID and user.get("tenant_id") != ALLOWED_TENANT_ID:
        return False
    return REQUIRED_APP_ROLE in user.get("roles", [])


def require_access(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not AUTH_REQUIRED:
            return view(*args, **kwargs)

        user = parse_client_principal()
        if not user:
            login_url = "/.auth/login/aad?post_login_redirect_uri=" + quote(request.url, safe="")
            return redirect(login_url)

        if not has_required_access(user):
            return render_template(
                "unauthorized.html",
                page_name="unauthorized",
                user=user,
                required_role=REQUIRED_APP_ROLE,
                allowed_tenant=ALLOWED_TENANT_ID,
            ), 403

        g.current_user = user
        return view(*args, **kwargs)

    return wrapped


@app.before_request
def attach_user():
    g.current_user = parse_client_principal()


@app.context_processor
def inject_layout_context():
    return {
        "current_user": getattr(g, "current_user", None),
        "app_title": "Mono's Tasks Management",
        "required_role": REQUIRED_APP_ROLE,
        "auth_required": AUTH_REQUIRED,
    }


@app.route("/")
@require_access
def home():
    tickets = [ticket for ticket in load_tickets() if ticket["status"] != "Closed"]
    open_tickets = [ticket for ticket in tickets if ticket["status"] == "Open"]
    in_progress_tickets = [ticket for ticket in tickets if ticket["status"] == "In Progress"]
    owners = sorted({ticket["assigned_to"] for ticket in tickets if ticket["assigned_to"]})

    return render_template(
        "index.html",
        page_name="dashboard",
        tickets=sorted(tickets, key=lambda item: int(item["id"]), reverse=True),
        open_count=len(open_tickets),
        in_progress_count=len(in_progress_tickets),
        total_count=len(tickets),
        owners=owners,
    )


@app.route("/create", methods=["GET", "POST"])
@require_access
def create_ticket():
    if request.method == "POST":
        data = request.form
        new_ticket = {
            "id": data["id"],
            "headline": data["headline"],
            "assigned_to": data["assigned_to"],
            "status": data["status"],
            "description": data["description"],
            "notes": data["notes"],
            "date_opened": now_date(),
            "date_closed": None,
        }
        get_container().create_item(new_ticket)
        return redirect(url_for("home"))

    return render_template("create.html", page_name="create", ticket_id=next_ticket_id())


@app.route("/submit_ticket", methods=["GET", "POST"])
def submit_ticket():
    if request.method == "POST":
        data = request.form
        new_ticket = {
            "id": str(next_ticket_id()),
            "headline": data["headline"],
            "assigned_to": "Mono Operations",
            "status": "Open",
            "description": (
                f"Customer Tenant Name: {data['tenant_name']}\n"
                f"Domain: {data['tenant_domain']}\n"
                f"Contact: {data['contact_name']} {data['contact_family']}\n"
                f"Phone: {data['phone']}\n"
                f"Email: {data['email']}\n"
                f"Service: {data['service']}\n\n"
                f"Description: {data['description']}"
            ),
            "notes": "Submitted via customer form",
            "date_opened": now_date(),
            "date_closed": None,
        }
        get_container().create_item(new_ticket)
        return render_template(
            "submit_ticket.html",
            page_name="submit",
            ticket_id=new_ticket["id"],
        )

    return render_template("submit_ticket.html", page_name="submit")


@app.route("/closed")
@require_access
def closed():
    tickets = [ticket for ticket in load_tickets() if ticket["status"] == "Closed"]
    return render_template(
        "closed.html",
        page_name="closed",
        tickets=sorted(tickets, key=lambda item: int(item["id"]), reverse=True),
    )


@app.route("/reopen/<ticket_id>", methods=["POST"])
@require_access
def reopen_ticket(ticket_id):
    try:
        ticket = find_ticket(ticket_id)
        ticket["status"] = "Open"
        ticket["date_closed"] = None
        get_container().upsert_item(ticket)
        if request.headers.get("X-Requested-With") == "fetch":
            return jsonify({"success": True})
        return redirect(url_for("closed"))
    except Exception as exc:
        if request.headers.get("X-Requested-With") == "fetch":
            return jsonify({"success": False, "error": str(exc)}), 400
        return f"Error: {exc}", 400


@app.route("/update", methods=["POST"])
@require_access
def update_ticket():
    try:
        data = request.get_json()
        ticket = find_ticket(data["id"])
        field = data["field"]
        value = data["value"]
        if field not in {"headline", "assigned_to", "status", "description", "notes"}:
            abort(400)

        ticket[field] = value
        if field == "status" and value == "Closed":
            ticket["date_closed"] = now_date()
        elif field == "status" and value != "Closed":
            ticket["date_closed"] = None

        get_container().upsert_item(ticket)
        return jsonify({"success": True, "ticket": ticket})
    except Exception as exc:
        print(f"Error occurred while updating ticket: {exc}")
        return jsonify({"success": False, "error": str(exc)}), 400


@app.route("/health")
def health():
    return jsonify({"status": "ok"})


@app.route("/logout")
def logout():
    if AUTH_REQUIRED and request.headers.get("X-MS-CLIENT-PRINCIPAL"):
        return redirect("/.auth/logout")
    return redirect(url_for("home"))


if __name__ == "__main__":
    print("Running the Flask app...")
    app.run(debug=True)
