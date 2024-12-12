from flask import Flask, render_template, request, redirect, url_for, jsonify
from azure.cosmos import CosmosClient
from datetime import datetime
import pytz
import os
COSMOS_DB_KEY = os.getenv("COSMOS_DB_KEY")
app = Flask(__name__)

# Israel timezone
israel_tz = pytz.timezone('Asia/Jerusalem')

# Cosmos DB configuration
COSMOS_DB_URI = "https://ticketingabnet.documents.azure.com:443/"
COSMOS_DB_KEY = "REPLACED_SECRET"
DATABASE_NAME = "ticketingdb"
CONTAINER_NAME = "ticketingdbcont"

client = CosmosClient(COSMOS_DB_URI, COSMOS_DB_KEY)
database = client.get_database_client(DATABASE_NAME)
container = database.get_container_client(CONTAINER_NAME)


@app.route('/')
def home():
    tickets = list(container.read_all_items())
    tickets = [t for t in tickets if t['status'] != 'Closed']
    open_tickets = [t for t in tickets if t['status'] == 'Open']
    in_progress_tickets = [t for t in tickets if t['status'] == 'In Progress']

    return render_template(
        'index.html',
        headline="ABnet Microsoft Management System",
        open_count=len(open_tickets),
        in_progress_count=len(in_progress_tickets),
        tickets=tickets
    )


@app.route('/create', methods=['GET', 'POST'])
def create_ticket():
    if request.method == 'POST':
        data = request.form
        # Automatically set the current date for 'date_opened' in Israel timezone
        date_opened = datetime.now(israel_tz).strftime('%d/%m/%Y')
        ticket_id = data['id']

        new_ticket = {
            "id": ticket_id,
            "headline": data['headline'],
            "assigned_to": data['assigned_to'],
            "status": data['status'],
            "description": data['description'],
            "notes": data['notes'],
            "date_opened": date_opened,  # Add date opened
            "date_closed": None         # Initially, date_closed is None
        }
        container.create_item(new_ticket)
        return redirect(url_for('home'))

    # Generate the next ticket ID
    tickets = list(container.read_all_items())
    next_ticket_id = max([int(t['id']) for t in tickets], default=0) + 1
    return render_template('create.html', ticket_id=next_ticket_id)


@app.route('/closed')
def closed():
    tickets = list(container.read_all_items())
    closed_tickets = [t for t in tickets if t['status'] == 'Closed']
    return render_template('closed.html', headline="ABnet Microsoft Management System", tickets=closed_tickets)


@app.route('/reopen/<ticket_id>', methods=['POST'])
def reopen_ticket(ticket_id):
    try:
        query = f"SELECT * FROM c WHERE c.id = '{ticket_id}'"
        ticket_list = list(container.query_items(query=query, enable_cross_partition_query=True))
        if not ticket_list:
            raise Exception(f"Ticket with ID {ticket_id} not found.")
        
        ticket = ticket_list[0]
        ticket['status'] = 'Open'
        ticket['date_closed'] = None  # Remove date closed when reopened
        container.upsert_item(body=ticket)
        return redirect(url_for('closed'))
    except Exception as e:
        print(f"Error occurred while reopening ticket: {e}")
        return f"Error: {e}", 400


@app.route('/update', methods=['POST'])
def update_ticket():
    try:
        data = request.get_json()
        ticket_id = data['id']
        field = data['field']
        value = data['value']

        query = f"SELECT * FROM c WHERE c.id = '{ticket_id}'"
        ticket_list = list(container.query_items(query=query, enable_cross_partition_query=True))
        if not ticket_list:
            raise Exception(f"Ticket with ID {ticket_id} not found.")
        
        ticket = ticket_list[0]
        ticket[field] = value

        # Update 'date_closed' if status is changed to 'Closed'
        if field == 'status' and value == 'Closed':
            ticket['date_closed'] = datetime.now(israel_tz).strftime('%d/%m/%Y')
        elif field == 'status' and value != 'Closed':
            ticket['date_closed'] = None  # Reset if reopened or status changed

        container.upsert_item(body=ticket)
        return jsonify({"success": True})
    except Exception as e:
        print(f"Error occurred while updating ticket: {e}")
        return jsonify({"success": False, "error": str(e)}), 400


# This block is required to run the Flask app
if __name__ == "__main__":
    print("Running the Flask app...")
    app.run(debug=True)
