from enum import Enum
from flask import Flask, jsonify, session, render_template
from flask_socketio import SocketIO, emit
from flask_session import Session
from flask_cors import CORS

import random
import threading
import uuid


# In-memory question bank
from polls import polls

# Set up Flask app
app = Flask(__name__)
app.config['SECRET_KEY'] = 'aertf7896234987fsdhudsf&*TY@WEUIS!'
app.config['SESSION_TYPE'] = 'filesystem'
app.config['CORS_HEADERS'] = 'Content-Type'
cors = CORS(app, resources={r"/*": {"origins": "*"}})
Session(app)
socketio = SocketIO(app, cors_allowed_origins="*", manage_session=False)

# Set up app state
Class_State = Enum('Class_State', ['NOT_RUNNING','STARTING_SOON','POLL_OPENS','POLL_CLOSES','POLL_ANSWER'])
class_state = Class_State.STARTING_SOON
poll_id_state = None
poll_token_state = None
client_votes = {} # client_votes[poll_id][client_id]

# Enable multi-threading
lock = threading.Lock()

'''
Direct the server to start accepting poll responses
'''
@app.route('/control/poll_open/<poll_id>', methods=['GET'])
def poll_start(poll_id):
    with lock:
        if not poll_id in polls:
            return jsonify({'success': False, 'message': 'poll_id not found'})
        
        global class_state, poll_id_state, poll_token_state
        class_state = Class_State.POLL_OPENS
        poll_id_state = poll_id
        poll_token_state = random.randint(100000, 999999)
        
        socketio.emit('update', state_to_json()) # broadcast to all
        
        return jsonify({'success': True, 'message': 'Poll started'})

'''
Direct the server to stop accepting poll responses, answer not revealed yet.
This state could be skipped, from POLL_OPENS to POLL_ANSWER
'''
@app.route('/control/poll_close/<poll_id>', methods=['GET'])
def poll_close(poll_id):
    with lock:
        if not poll_id in polls:
            return jsonify({'success': False, 'message': 'poll_id not found'})
        
        global class_state, poll_id_state
        class_state = Class_State.POLL_CLOSES
        poll_id_state = poll_id

        socketio.emit('update', state_to_json()) # broadcast to all

        return jsonify({'success': True, 'message': 'Poll stopped'})

'''
Direct the server to stop accepting poll responses and reveal answer
'''
@app.route('/control/poll_answer/<poll_id>', methods=['GET'])
def poll_result(poll_id):
    with lock:
        if not poll_id in polls:
            return jsonify({'success': False, 'message': 'poll_id not found'})
        
        global class_state, poll_id_state
        class_state = Class_State.POLL_ANSWER
        poll_id_state = poll_id

        socketio.emit('update', state_to_json()) # broadcast to all

        return jsonify({'success': True, 'message': 'Answer revealed'})


#######################################################################################

'''
Home page for clients (static)
'''
@app.route('/')
def home():
    print("Home", session.get('client_id'))
    if 'client_id' not in session:
        session['client_id'] = str(uuid.uuid4())
        print(f"Assigned new client_id: {session['client_id']}")
    else:
        print(f"Existing session with client_id: {session['client_id']}")

    with lock:
        return render_template('index.html')

'''
Client connects to websocket
'''
@socketio.on('connect')
def handle_connect():
    print("WS", session.get('client_id'))
    client_id = session.get('client_id')
    if client_id:
        print(f"WebSocket connected with client_id: {client_id}")
        # emit('update', {'client_id': client_id, 'message': 'Connected successfully!'})
    else:
        print("WebSocket connection with unknown client.")
        # emit('error', {'message': 'Session not initialized'})

    # if 'client_id' not in session:
    #     print("socket create session")
    #     # session.permanent = False # Only expires when browser closes TODO not working, still can submit answer after refresh
    #     session['client_id'] = str(uuid.uuid4())
    #     # session.modified = True # Ensure this modification is saved
    emit('update', state_to_json()) # Client context aware

'''
Client requests for state
'''
@socketio.on('getstate')
def handle_getstate():
    emit('update', state_to_json()) # Client context aware

'''
Client votes for a poll option
'''
@socketio.on('vote')
def handle_vote(data):
    # Check if poll is still open
    if not class_state == Class_State.POLL_OPENS:
        emit('invalid') # Client context aware
        return
    
    # Check if poll token matches
    if not data['poll_token'] == poll_token_state:
        emit('invalid') # Client context aware
        return

    # Check if response is valid
    if not 0 <= data['option_index'] < len(polls[poll_id_state].get('options',[])):
        emit('invalid') # Client context aware
        return
    
    # Check if client session exists
    client_id = session.get('client_id')
    if client_id is None:
        emit('invalid') # Client context aware
        return

    # Check if client is editing response and editing is allowed
    client_existing_option_index = client_votes.get(poll_id_state,{}).get(client_id)
    response_editable = polls[poll_id_state].get('response_editable',False)
    if (client_existing_option_index is not None) and not response_editable:
        emit('invalid') # Client context aware
        return

    # Process the vote
    with lock:
        option_index = data['option_index']
        if client_existing_option_index is not None:
            # Reverse the previous vote
            polls[poll_id_state]['votes'][client_existing_option_index] -= 1

        client_votes.setdefault(poll_id_state, {})[client_id] = option_index
        polls[poll_id_state]['votes'][option_index] += 1

        socketio.emit('update', state_to_json()) # broadcast to all

'''
Convert current poll state to json to be broadcasted to clients
'''
def state_to_json():
    match class_state:
        case Class_State.STARTING_SOON:
            # Nothing to show
            return {
                'state': class_state.name
            }
        case Class_State.POLL_OPENS | Class_State.POLL_CLOSES:
            # Show question/options and question/votes
            poll = polls[poll_id_state]
            _,anonymised_votes = anonymise_optionsVotes(poll_id=poll_id_state)
            return {
                'state': class_state.name,
                'poll_token': poll_token_state,
                'question': poll['question'],
                'options': poll['options'],
                'response_editable': poll['response_editable'],
                'anonymised_votes': anonymised_votes
            }
        case Class_State.POLL_ANSWER:
            # Show question/options+votes/answer
            poll = polls[poll_id_state]
            anonymised_options,anonymised_votes = anonymise_optionsVotes(poll_id=poll_id_state)
            return {
                'state': class_state.name,
                'question': poll['question'],
                'options': poll['options'],
                'correct_answer': poll['correct_answer'],
                'anonymised_votes': anonymised_votes,
                'anonymised_options': anonymised_options
            }


'''
Anonymise the poll votes using a seed based on the poll_id
'''
def anonymise_optionsVotes(poll_id) -> tuple:
    if not poll_id in polls:
        raise ValueError(f'poll_id bad: {poll_id}')
    
    poll = polls[poll_id]
    options_votes = list(zip(poll['options'], poll['votes']))
    
    # Use a seed derived from the poll_id for predictable shuffling
    seed = hash(poll_id) & 0xffffffff #sum(ord(char) for char in poll_id)
    random.seed(seed)
    random.shuffle(options_votes)

    # Separate shuffled options and votes
    shuffled_options = [option for option,_ in options_votes]
    shuffled_votes = [votes for _,votes in options_votes]
    
    return shuffled_options, shuffled_votes



# def emit_poll(poll_id, show_results=False, final_results=False, client_id=None):
#     poll = polls[poll_id]
#     results = poll['votes']

#     if show_results:
#         # Shuffle options to anonymize them before sending to clients
#         shuffled_indices = list(range(len(poll['options'])))
#         random.shuffle(shuffled_indices)
#         shuffled_options = [poll['options'][i] for i in shuffled_indices]
#         shuffled_results = [results[i] for i in shuffled_indices]
#         response_data = {
#             'state': 'showing_results',
#             'question': poll['question'],
#             'shuffled_options': shuffled_options,
#             'shuffled_results': shuffled_results,
#             'majority': majority,
#             'client_vote': client_votes.get(client_id, {}).get('vote', None)
#         }
#     elif final_results:
#         response_data = {
#             'state': 'final_results',
#             'question': poll['question'],
#             'options': poll['options'],
#             'results': results,
#             'correct_answer': poll['correct_answer'],
#             'client_vote': client_votes.get(client_id, {}).get('vote', None)
#         }
#     else:
#         response_data = {
#             'state': 'accepting_answers',
#             'question': poll['question'],
#             'options': poll['options']
#         }

#     if client_id:
#         emit('update', response_data, room=client_id)
#     else:
#         socketio.emit('update', response_data, room=poll_id)

if __name__ == '__main__':
    socketio.run(app, debug=True)