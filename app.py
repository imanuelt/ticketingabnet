import base64
import json
import os
from math import ceil
from urllib import request as urlrequest
from datetime import datetime, timedelta
from functools import wraps
from urllib.parse import quote, urlencode

import pytz
from azure.cosmos import CosmosClient
from azure.identity import ManagedIdentityCredential
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
APP_AUTH_ENTERPRISE_APP_OBJECT_ID = os.getenv("APP_AUTH_ENTERPRISE_APP_OBJECT_ID", "")

PRIORITY_OPTIONS = ["Low", "Medium", "High", "Urgent"]
CATEGORY_OPTIONS = ["Incident", "Access", "Service Request", "Change", "Billing", "Question"]
SERVICE_OPTIONS = ["Azure", "Microsoft 365", "Security", "Marketplace", "Billing", "Operations", "Other"]
STATUS_OPTIONS = ["Open", "In Progress", "Closed"]
SORT_OPTIONS = {
    "sla": "SLA focus",
    "priority": "Priority",
    "updated": "Last updated",
    "newest": "Newest first",
}
PRIORITY_RANK = {"Low": 0, "Medium": 1, "High": 2, "Urgent": 3}
SLA_TARGET_HOURS = {"Low": 72, "Medium": 24, "High": 8, "Urgent": 2}
SLA_STATE_ORDER = {"overdue": 0, "due-soon": 1, "on-track": 2, "resolved": 3}
PERSISTED_FIELDS = {
    "id",
    "headline",
    "assigned_to",
    "assigned_to_id",
    "status",
    "description",
    "notes",
    "date_opened",
    "date_closed",
    "opened_at",
    "updated_at",
    "closed_at",
    "priority",
    "category",
    "service",
    "source",
    "requester_name",
    "requester_email",
    "company_name",
    "tenant_domain",
    "phone",
    "sla_due_at",
}

container = None
assignable_users_cache = {"users": [], "loaded_at": None}


def now_dt():
    return datetime.now(israel_tz)


def now_iso():
    return now_dt().isoformat()


def now_date():
    return now_dt().strftime("%d/%m/%Y")


def parse_iso(value):
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else israel_tz.localize(parsed)


def parse_legacy_date(value):
    if not value:
        return None
    try:
        parsed = datetime.strptime(value, "%d/%m/%Y")
    except ValueError:
        return None
    return israel_tz.localize(parsed)


def format_dt(value):
    parsed = parse_iso(value) or parse_legacy_date(value)
    return parsed.strftime("%d %b %Y, %H:%M") if parsed else "Not set"


def short_duration(delta):
    total_minutes = max(int(delta.total_seconds() // 60), 0)
    days, remainder = divmod(total_minutes, 1440)
    hours, minutes = divmod(remainder, 60)
    if days:
        return f"{days}d {hours}h"
    if hours:
        return f"{hours}h {minutes}m"
    return f"{minutes}m"


def default_sla_due(opened_at, priority):
    return opened_at + timedelta(hours=SLA_TARGET_HOURS.get(priority, 24))


def normalize_priority(value):
    return value if value in PRIORITY_OPTIONS else "Medium"


def normalize_category(value):
    return value if value in CATEGORY_OPTIONS else "Service Request"


def normalize_service(value):
    return value if value in SERVICE_OPTIONS else "Operations"


def normalize_status(value):
    return value if value in STATUS_OPTIONS else "Open"


def serialize_ticket(ticket):
    payload = {}
    for key in PERSISTED_FIELDS:
        if key in ticket:
            payload[key] = ticket[key]
    return payload


def normalize_ticket(ticket):
    normalized = dict(ticket)
    opened_at = parse_iso(normalized.get("opened_at")) or parse_legacy_date(normalized.get("date_opened")) or now_dt()
    updated_at = parse_iso(normalized.get("updated_at")) or opened_at
    closed_at = parse_iso(normalized.get("closed_at")) or parse_legacy_date(normalized.get("date_closed"))
    status = normalize_status(normalized.get("status"))
    priority = normalize_priority(normalized.get("priority"))
    category = normalize_category(normalized.get("category"))
    service = normalize_service(normalized.get("service"))
    sla_due_at = parse_iso(normalized.get("sla_due_at")) or default_sla_due(opened_at, priority)

    if status == "Closed":
        sla_state = "resolved"
    elif now_dt() > sla_due_at:
        sla_state = "overdue"
    elif sla_due_at - now_dt() <= timedelta(hours=4):
        sla_state = "due-soon"
    else:
        sla_state = "on-track"

    normalized.update(
        {
            "status": status,
            "priority": priority,
            "category": category,
            "service": service,
            "assigned_to_id": normalized.get("assigned_to_id") or "",
            "source": normalized.get("source") or "Internal Desk",
            "requester_name": normalized.get("requester_name") or "Mano Team",
            "requester_email": normalized.get("requester_email") or "",
            "company_name": normalized.get("company_name") or "",
            "tenant_domain": normalized.get("tenant_domain") or "",
            "phone": normalized.get("phone") or "",
            "date_opened": normalized.get("date_opened") or opened_at.strftime("%d/%m/%Y"),
            "date_closed": normalized.get("date_closed") or (closed_at.strftime("%d/%m/%Y") if closed_at else None),
            "opened_at": opened_at.isoformat(),
            "updated_at": updated_at.isoformat(),
            "closed_at": closed_at.isoformat() if closed_at else None,
            "sla_due_at": sla_due_at.isoformat(),
            "display_opened_at": format_dt(opened_at.isoformat()),
            "display_updated_at": format_dt(updated_at.isoformat()),
            "display_due_at": format_dt(sla_due_at.isoformat()),
            "age_label": short_duration(now_dt() - opened_at),
            "sla_state": sla_state,
            "sla_label": {
                "resolved": "Resolved",
                "overdue": "Overdue",
                "due-soon": "Due Soon",
                "on-track": "On Track",
            }[sla_state],
            "description_excerpt": (normalized.get("description") or "").strip()[:160],
            "notes_excerpt": (normalized.get("notes") or "").strip()[:120],
        }
    )
    return normalized


def get_graph_credential():
    return ManagedIdentityCredential()


def load_assignable_users_from_graph():
    if not APP_AUTH_ENTERPRISE_APP_OBJECT_ID:
        return []

    token = get_graph_credential().get_token("https://graph.microsoft.com/.default")
    next_url = (
        "https://graph.microsoft.com/v1.0/servicePrincipals/"
        f"{APP_AUTH_ENTERPRISE_APP_OBJECT_ID}/appRoleAssignedTo"
        "?$select=principalId,principalDisplayName,principalType"
    )
    users = []
    seen = set()

    while next_url:
        req = urlrequest.Request(
            next_url,
            headers={
                "Authorization": f"Bearer {token.token}",
                "Accept": "application/json",
            },
        )
        with urlrequest.urlopen(req, timeout=15) as response:
            payload = json.loads(response.read().decode("utf-8"))

        for item in payload.get("value", []):
            if item.get("principalType") != "User":
                continue
            object_id = item.get("principalId")
            display_name = item.get("principalDisplayName")
            if not object_id or not display_name or object_id in seen:
                continue
            seen.add(object_id)
            users.append({"id": object_id, "name": display_name})

        next_url = payload.get("@odata.nextLink")

    return sorted(users, key=lambda user: user["name"].lower())


def get_assignable_users():
    cache_time = assignable_users_cache["loaded_at"]
    if cache_time and now_dt() - cache_time < timedelta(minutes=10):
        return assignable_users_cache["users"]

    users = []
    try:
        users = load_assignable_users_from_graph()
    except Exception as exc:
        print(f"Unable to load assignable users from Microsoft Graph: {exc}")

    if not users:
        fallback_names = sorted({ticket["assigned_to"] for ticket in load_tickets() if ticket.get("assigned_to")})
        users = [{"id": "", "name": name} for name in fallback_names]

    assignable_users_cache["users"] = users
    assignable_users_cache["loaded_at"] = now_dt()
    return users


def ensure_assignment_option(users, ticket):
    assigned_name = ticket.get("assigned_to", "").strip()
    assigned_id = ticket.get("assigned_to_id", "").strip()
    if not assigned_name:
        return users
    if any(user["name"] == assigned_name or (assigned_id and user["id"] == assigned_id) for user in users):
        return users
    return [{"id": assigned_id, "name": assigned_name}] + users


def resolve_assignment(form_or_payload):
    assigned_to_id = (form_or_payload.get("assigned_to_id") or "").strip()
    assigned_to = (form_or_payload.get("assigned_to") or "").strip()
    for user in get_assignable_users():
        if assigned_to_id and user["id"] == assigned_to_id:
            return user["name"], user["id"]
        if assigned_to and user["name"] == assigned_to:
            return user["name"], user["id"]
    return assigned_to, assigned_to_id


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


def load_tickets():
    return [normalize_ticket(ticket) for ticket in get_container().read_all_items()]


def sorted_tickets():
    return sorted(load_tickets(), key=lambda item: int(item["id"]), reverse=True)


def next_ticket_id():
    tickets = load_tickets()
    return max([int(ticket["id"]) for ticket in tickets], default=0) + 1


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
    priority = request.args.get("priority", "")
    category = request.args.get("category", "")
    service = request.args.get("service", "")
    requester = request.args.get("requester", "").strip()
    sort = request.args.get("sort", "sla")
    valid_statuses = {"", *STATUS_OPTIONS}

    if status not in valid_statuses:
        status = default_status
    if priority and priority not in PRIORITY_OPTIONS:
        priority = ""
    if category and category not in CATEGORY_OPTIONS:
        category = ""
    if service and service not in SERVICE_OPTIONS:
        service = ""
    if sort not in SORT_OPTIONS:
        sort = "sla"

    page = request.args.get("page", "1")
    try:
        page = max(int(page), 1)
    except ValueError:
        page = 1

    return {
        "search": request.args.get("q", "").strip(),
        "owner": request.args.get("owner", "").strip(),
        "status": status,
        "priority": priority,
        "category": category,
        "service": service,
        "requester": requester,
        "sort": sort,
        "page": page,
    }


def ticket_search_blob(ticket):
    return " ".join(
        [
            ticket.get("headline", ""),
            ticket.get("assigned_to", ""),
            ticket.get("description", ""),
            ticket.get("notes", ""),
            ticket.get("requester_name", ""),
            ticket.get("requester_email", ""),
            ticket.get("service", ""),
            ticket.get("category", ""),
            ticket.get("priority", ""),
            ticket.get("source", ""),
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
        if filters["requester"] and ticket.get("requester_name") != filters["requester"]:
            continue
        if filters["priority"] and ticket.get("priority") != filters["priority"]:
            continue
        if filters["category"] and ticket.get("category") != filters["category"]:
            continue
        if filters["service"] and ticket.get("service") != filters["service"]:
            continue
        if search_value and search_value not in ticket_search_blob(ticket):
            continue
        filtered.append(ticket)

    return filtered


def sort_tickets(tickets, sort_key):
    if sort_key == "priority":
        return sorted(tickets, key=lambda ticket: (-PRIORITY_RANK[ticket["priority"]], -int(ticket["id"])))
    if sort_key == "updated":
        return sorted(tickets, key=lambda ticket: parse_iso(ticket["updated_at"]) or now_dt(), reverse=True)
    if sort_key == "newest":
        return sorted(tickets, key=lambda ticket: int(ticket["id"]), reverse=True)
    return sorted(
        tickets,
        key=lambda ticket: (
            SLA_STATE_ORDER[ticket["sla_state"]],
            -PRIORITY_RANK[ticket["priority"]],
            -(parse_iso(ticket["updated_at"]) or now_dt()).timestamp(),
        ),
    )


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
        "priority_options": PRIORITY_OPTIONS,
        "category_options": CATEGORY_OPTIONS,
        "service_options": SERVICE_OPTIONS,
        "sort_options": SORT_OPTIONS,
        "status_options": STATUS_OPTIONS,
    }


@app.route("/")
@require_access
def home():
    all_tickets = sorted_tickets()
    assignable_users = get_assignable_users()
    filters = parse_ticket_filters()
    filtered_tickets = sort_tickets(filter_tickets(all_tickets, filters), filters["sort"])
    pagination = build_pagination(len(filtered_tickets), filters["page"])
    tickets = filtered_tickets[pagination["start_index"]:pagination["end_index"]]

    open_tickets = [ticket for ticket in all_tickets if ticket["status"] == "Open"]
    in_progress_tickets = [ticket for ticket in all_tickets if ticket["status"] == "In Progress"]
    closed_tickets = [ticket for ticket in all_tickets if ticket["status"] == "Closed"]
    urgent_tickets = [ticket for ticket in all_tickets if ticket["status"] != "Closed" and ticket["priority"] == "Urgent"]
    overdue_tickets = [ticket for ticket in all_tickets if ticket["status"] != "Closed" and ticket["sla_state"] == "overdue"]
    due_soon_tickets = [ticket for ticket in all_tickets if ticket["status"] != "Closed" and ticket["sla_state"] == "due-soon"]
    unassigned_tickets = [ticket for ticket in all_tickets if not ticket["assigned_to"].strip()]
    owners = sorted({ticket["assigned_to"] for ticket in all_tickets if ticket["assigned_to"]})
    requesters = sorted({ticket["requester_name"] for ticket in all_tickets if ticket["requester_name"]})
    focus_tickets = sort_tickets(
        [ticket for ticket in all_tickets if ticket["status"] != "Closed" and ticket["sla_state"] in {"overdue", "due-soon"}],
        "sla",
    )[:5]
    tickets = [normalize_ticket({**ticket, "assignment_options": ensure_assignment_option(assignable_users, ticket)}) for ticket in tickets]

    return render_template(
        "index.html",
        page_name="dashboard",
        tickets=tickets,
        open_count=len(open_tickets),
        in_progress_count=len(in_progress_tickets),
        closed_count=len(closed_tickets),
        total_count=len(all_tickets),
        urgent_count=len(urgent_tickets),
        overdue_count=len(overdue_tickets),
        due_soon_count=len(due_soon_tickets),
        unassigned_count=len(unassigned_tickets),
        owners=owners,
        requesters=requesters,
        filters=filters,
        filtered_count=len(filtered_tickets),
        pagination=pagination,
        focus_tickets=focus_tickets,
        assignable_users=assignable_users,
    )


@app.route("/create", methods=["GET", "POST"])
@require_access
def create_ticket():
    if request.method == "POST":
        data = request.form
        opened_at = now_dt()
        priority = normalize_priority(data.get("priority"))
        assigned_to, assigned_to_id = resolve_assignment(data)
        new_ticket = {
            "id": data["id"],
            "headline": data["headline"],
            "assigned_to": assigned_to,
            "assigned_to_id": assigned_to_id,
            "status": normalize_status(data["status"]),
            "priority": priority,
            "category": normalize_category(data.get("category")),
            "service": normalize_service(data.get("service")),
            "requester_name": data.get("requester_name") or (g.current_user.get("name") if g.current_user else "Mano Team"),
            "requester_email": data.get("requester_email") or (g.current_user.get("email") if g.current_user else ""),
            "source": "Internal Desk",
            "description": data["description"],
            "notes": data["notes"],
            "date_opened": opened_at.strftime("%d/%m/%Y"),
            "date_closed": None,
            "opened_at": opened_at.isoformat(),
            "updated_at": opened_at.isoformat(),
            "closed_at": None,
            "sla_due_at": default_sla_due(opened_at, priority).isoformat(),
        }
        get_container().create_item(serialize_ticket(new_ticket))
        return redirect(url_for("home"))

    return render_template(
        "create.html",
        page_name="create",
        ticket_id=next_ticket_id(),
        assignable_users=get_assignable_users(),
    )


@app.route("/submit_ticket", methods=["GET", "POST"])
@require_access
def submit_ticket():
    if request.method == "POST":
        data = request.form
        opened_at = now_dt()
        priority = normalize_priority(data.get("priority"))
        requester_name = f"{data['contact_name']} {data['contact_family']}".strip()
        new_ticket = {
            "id": str(next_ticket_id()),
            "headline": data["headline"],
            "assigned_to": "Mano Operations",
            "assigned_to_id": "",
            "status": "Open",
            "priority": priority,
            "category": normalize_category(data.get("category")),
            "service": normalize_service(data.get("service")),
            "source": "Customer Intake",
            "requester_name": requester_name or "Customer Contact",
            "requester_email": data["email"],
            "company_name": data["tenant_name"],
            "tenant_domain": data["tenant_domain"],
            "phone": data["phone"],
            "description": (
                f"Tenant Name: {data['tenant_name']}\n"
                f"Tenant Domain: {data['tenant_domain']}\n"
                f"Contact: {requester_name}\n"
                f"Phone: {data['phone']}\n"
                f"Email: {data['email']}\n"
                f"Service: {data['service']}\n"
                f"Category: {data['category']}\n\n"
                f"Description: {data['description']}"
            ),
            "notes": "Submitted via customer intake form",
            "date_opened": opened_at.strftime("%d/%m/%Y"),
            "date_closed": None,
            "opened_at": opened_at.isoformat(),
            "updated_at": opened_at.isoformat(),
            "closed_at": None,
            "sla_due_at": default_sla_due(opened_at, priority).isoformat(),
        }
        get_container().create_item(serialize_ticket(new_ticket))
        return render_template(
            "submit_ticket.html",
            page_name="submit",
            ticket_id=new_ticket["id"],
            assignable_users=get_assignable_users(),
        )

    return render_template("submit_ticket.html", page_name="submit", assignable_users=get_assignable_users())


@app.route("/closed")
@require_access
def closed():
    all_tickets = [ticket for ticket in sorted_tickets() if ticket["status"] == "Closed"]
    filters = parse_ticket_filters(default_status="Closed")
    filtered_tickets = sort_tickets(filter_tickets(all_tickets, filters), filters["sort"])
    pagination = build_pagination(len(filtered_tickets), filters["page"])
    tickets = filtered_tickets[pagination["start_index"]:pagination["end_index"]]

    return render_template(
        "closed.html",
        page_name="closed",
        tickets=tickets,
        owners=sorted({ticket["assigned_to"] for ticket in all_tickets if ticket["assigned_to"]}),
        requesters=sorted({ticket["requester_name"] for ticket in all_tickets if ticket["requester_name"]}),
        filters=filters,
        filtered_count=len(filtered_tickets),
        pagination=pagination,
    )


@app.route("/reopen/<ticket_id>", methods=["POST"])
@require_access
def reopen_ticket(ticket_id):
    try:
        ticket = normalize_ticket(find_ticket(ticket_id))
        ticket["status"] = "Open"
        ticket["date_closed"] = None
        ticket["closed_at"] = None
        ticket["updated_at"] = now_iso()
        ticket["sla_due_at"] = default_sla_due(parse_iso(ticket["opened_at"]) or now_dt(), ticket["priority"]).isoformat()
        get_container().upsert_item(serialize_ticket(ticket))
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
        ticket = normalize_ticket(find_ticket(data["id"]))
        field = data["field"]
        value = data["value"]
        if field not in {"headline", "assignment", "status", "description", "notes", "priority", "category", "service"}:
            abort(400)

        if field == "assignment":
            assigned_to, assigned_to_id = resolve_assignment(value if isinstance(value, dict) else {})
            ticket["assigned_to"] = assigned_to
            ticket["assigned_to_id"] = assigned_to_id
        elif field == "status":
            value = normalize_status(value)
            ticket[field] = value
        elif field == "priority":
            value = normalize_priority(value)
            ticket[field] = value
        elif field == "category":
            value = normalize_category(value)
            ticket[field] = value
        elif field == "service":
            value = normalize_service(value)
            ticket[field] = value
        else:
            ticket[field] = value
        ticket["updated_at"] = now_iso()

        if field == "status" and value == "Closed":
            ticket["date_closed"] = now_date()
            ticket["closed_at"] = now_iso()
        elif field == "status" and value != "Closed":
            ticket["date_closed"] = None
            ticket["closed_at"] = None

        if field == "priority":
            opened_at = parse_iso(ticket["opened_at"]) or now_dt()
            ticket["sla_due_at"] = default_sla_due(opened_at, ticket["priority"]).isoformat()

        get_container().upsert_item(serialize_ticket(ticket))
        return jsonify({"success": True, "ticket": normalize_ticket(ticket)})
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
