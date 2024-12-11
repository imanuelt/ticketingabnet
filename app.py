from flask import Flask, render_template
from azure.cosmos import CosmosClient

app = Flask(__name__)

# Replace with your Cosmos DB details
COSMOS_DB_URI = "https://your-cosmosdb-account.documents.azure.com:443/"
COSMOS_DB_KEY = "your-cosmosdb-key"
DATABASE_NAME = "TicketsDatabase"
CONTAINER_NAME = "TicketsContainer"

client = CosmosClient(COSMOS_DB_URI, COSMOS_DB_KEY)
database = client.get_database_client(DATABASE_NAME)
container = database.get_container_client(CONTAINER_NAME)

@app.route('/')
def home():
    # Fetch tickets from Cosmos DB
    tickets = list(container.read_all_items())
    open_tickets = [t for t in tickets if t['status'] == 'Open']
    in_progress_tickets = [t for t in tickets if t['status'] == 'In Progress']
    closed_tickets = [t for t in tickets if t['status'] == 'Closed']
    
    return render_template(
        'index.html',
        open_count=len(open_tickets),
        in_progress_count=len(in_progress_tickets),
        closed_count=len(closed_tickets),
        tickets=tickets
    )

@app.route('/closed')
def closed():
    # Fetch closed tickets from Cosmos DB
    tickets = list(container.read_all_items())
    closed_tickets = [t for t in tickets if t['status'] == 'Closed']
    
    return render_template('closed.html', tickets=closed_tickets)

if __name__ == '__main__':
    app.run(debug=True)
