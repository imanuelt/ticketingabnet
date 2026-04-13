import base64
import json
import os
from math import ceil
from datetime import datetime
from functools import wraps
from urllib.parse import quote, urlencode

import pytz
from azure.cosmos import CosmosClient
from dotenv import load_dotenv
from flask import Flask, abort, g, jsonify, redirect, render_template, request, url_for

load_dotenv()

app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET_KEY", "mano-tasks-dev-key")

israel_tz = pytz.timezone("Asia/Jerusalem")

COSMOS_DB_URI_ENV = "COSMOS_DB_URI"
COSMOS_DB_KEY_ENV = "COSMOS_DB_KEY"
DATABASE_NAME = os.getenv("COSMOS_DB_DATABASE", "ticketingdb")
CONTAINER_NAME = os.getenv("COSMOS_DB_CONTAINER", "ticketingdbcont")
AUTH_REQUIRED = os.getenv("AUTH_REQUIRED", "false").lower() == "true"
ALLOWED_TENANT_ID = os.getenv("ALLOWED_TENANT_ID")
REQUIRED_APP_ROLE = os.getenv("REQUIRED_APP_ROLE", "TaskUser")
DEV_AUTH_BYPASS = os.getenv("DEV_AUTH_BYPASS", "false").lower() == "true"
TICKETS_PER_PAGE = max(int(os.getenv("TICKETS_PER_PAGE", "18")), 1)

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


def sorted_tickets():
    return sorted(load_tickets(), key=lambda item: int(item["id"]), reverse=True)


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
            "email": "developer@mano.local",
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


def parse_ticket_filters(default_status=""):
    status = request.args.get("status", default_status)
    valid_statuses = {"", "Open", "In Progress", "Closed"}
    if status not in valid_statuses:
        status = default_status

    page = request.args.get("page", "1")
    try:
        page = max(int(page), 1)
    except ValueError:
        page = 1

    return {
        "search": request.args.get("q", "").strip(),
        "owner": request.args.get("owner", "").strip(),
        "status": status,
        "page": page,
    }


def ticket_search_blob(ticket):
    return " ".join(
        [
            ticket.get("headline", ""),
            ticket.get("assigned_to", ""),
            ticket.get("description", ""),
            ticket.get("notes", ""),
        ]
    ).lower()


def filter_tickets(tickets, filters):
    search_value = filters["search"].lower()
    filtered = []

    for ticket in tickets:
        if filters["status"] and ticket.get("status") != filters["status"]:
            continue
        if filters["owner"] and ticket.get("assigned_to") != filters["owner"]:
            continue
        if search_value and search_value not in ticket_search_blob(ticket):
            continue
        filtered.append(ticket)

    return filtered


def build_pagination(total_items, current_page):
    total_pages = max(ceil(total_items / TICKETS_PER_PAGE), 1)
    current_page = min(current_page, total_pages)
    start_index = (current_page - 1) * TICKETS_PER_PAGE
    end_index = start_index + TICKETS_PER_PAGE

    def page_url(page_number):
        params = request.args.to_dict(flat=True)
        params["page"] = page_number
        if page_number == 1:
            params.pop("page", None)
        query = urlencode({key: value for key, value in params.items() if value})
        return f"{request.path}?{query}" if query else request.path

    window_start = max(current_page - 2, 1)
    window_end = min(window_start + 4, total_pages)
    window_start = max(window_end - 4, 1)

    return {
        "current_page": current_page,
        "total_pages": total_pages,
        "start_index": start_index,
        "end_index": end_index,
        "pages": [
            {"number": page_number, "url": page_url(page_number)}
            for page_number in range(window_start, window_end + 1)
        ],
        "prev_url": page_url(current_page - 1) if current_page > 1 else None,
        "next_url": page_url(current_page + 1) if current_page < total_pages else None,
    }


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
        "app_title": "Mano's Tasks Management",
        "required_role": REQUIRED_APP_ROLE,
        "auth_required": AUTH_REQUIRED,
    }


@app.route("/")
@require_access
def home():
    all_tickets = sorted_tickets()
    filters = parse_ticket_filters()
    filtered_tickets = filter_tickets(all_tickets, filters)
    pagination = build_pagination(len(filtered_tickets), filters["page"])
    tickets = filtered_tickets[pagination["start_index"]:pagination["end_index"]]

    open_tickets = [ticket for ticket in all_tickets if ticket["status"] == "Open"]
    in_progress_tickets = [ticket for ticket in all_tickets if ticket["status"] == "In Progress"]
    closed_tickets = [ticket for ticket in all_tickets if ticket["status"] == "Closed"]
    owners = sorted({ticket["assigned_to"] for ticket in all_tickets if ticket["assigned_to"]})

    return render_template(
        "index.html",
        page_name="dashboard",
        tickets=tickets,
        open_count=len(open_tickets),
        in_progress_count=len(in_progress_tickets),
        closed_count=len(closed_tickets),
        total_count=len(all_tickets),
        owners=owners,
        filters=filters,
        filtered_count=len(filtered_tickets),
        pagination=pagination,
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
@require_access
def submit_ticket():
    if request.method == "POST":
        data = request.form
        new_ticket = {
            "id": str(next_ticket_id()),
            "headline": data["headline"],
            "assigned_to": "Mano Operations",
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
    all_tickets = [ticket for ticket in sorted_tickets() if ticket["status"] == "Closed"]
    filters = parse_ticket_filters(default_status="Closed")
    filtered_tickets = filter_tickets(all_tickets, filters)
    pagination = build_pagination(len(filtered_tickets), filters["page"])
    tickets = filtered_tickets[pagination["start_index"]:pagination["end_index"]]

    return render_template(
        "closed.html",
        page_name="closed",
        tickets=tickets,
        owners=sorted({ticket["assigned_to"] for ticket in all_tickets if ticket["assigned_to"]}),
        filters=filters,
        filtered_count=len(filtered_tickets),
        pagination=pagination,
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
