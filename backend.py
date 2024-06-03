from enum import Enum
from flask import Flask, jsonify, session, render_template
from flask_socketio import SocketIO, join_room
from flask_session import Session # enable server side session management
from flask_socketio import emit as emit_targeted # this is context sensitive emit
from flask_cors import CORS
from typing import Tuple

import json
import random
import redis
import threading
import uuid


# Configurations
REDIS_HOSTNAME = '127.0.0.1'
REDIS_PORT = 6379

# Set up Flask app
app = Flask(__name__)
app.config['SECRET_KEY'] = 'aertf7896234987fsdhudsf&*TY@WEUIS!'
app.config['CORS_HEADERS'] = 'Content-Type'
cors = CORS(app, resources={r"/*": {"origins": "*"}})
# Configure Redis for storing the session data on the server-side
app.config['SESSION_TYPE'] = 'redis'
app.config['SESSION_PERMANENT'] = False
app.config['SESSION_USE_SIGNER'] = True
app.config['SESSION_REDIS'] = redis.Redis(host=REDIS_HOSTNAME, port=REDIS_PORT, db=0)
Session(app)
socketio = SocketIO(app, cors_allowed_origins="*", manage_session=False)

# Redis for app data
r = redis.Redis(host=REDIS_HOSTNAME, port=REDIS_PORT, db=1, decode_responses=True)

# Enum
RoomState = Enum('RoomState', ['NOT_RUNNING','STARTING_SOON','POLL_OPENS','POLL_CLOSES','POLL_ANSWER'])



'''
Home page
'''
@app.route('/')
def home():
    return "Under construction"


'''
WebApp for clients (static)
'''
@app.route('/<room>', methods=['GET'])
def serve_client_page(room):
    # Create client_id (shared with websocket)
    if session.get('client_id') is None:
        session['client_id'] = str(uuid.uuid4())
        print(f"[{room}] New visitor: {session['client_id']}")
    else:
        print(f"[{room}] Returning visitor: {session['client_id']}")
    # Set room (shared with websocket)
    session['room'] = room

    return render_template('index.html')


'''
Client connects to websocket
'''
@socketio.on('connect')
def handle_connect():
    # Expect to get session from HTML Flask session
    client_id = session.get('client_id')
    room = session.get('room')
    if client_id is None or room is None:
        print("WS fail to share session.")
        emit_targeted('invalid', {'code':'WS_FAIL_TO_SHARE_SESSION'})
        return

    # Join room
    join_room(room)
    print(f"[{room}] WebSocket: client_id {client_id}")

    # Client context aware emit
    emit_targeted('myvote', clientvote_to_json(room=room))      # Votes placed by client (emit first)
    emit_targeted('update', state_to_json(room=room))           # Overall state of poll  (emit last)
        

'''
Client requests for state
'''
@socketio.on('getstate')
def handle_getstate():
    room = session.get('room')
    if room is None:
        print("WS fail to share session.")
        emit_targeted('invalid', {'code':'WS_FAIL_TO_SHARE_SESSION'})
        return
    emit_targeted('update', state_to_json(room=room))

'''
Client votes for a poll option
'''
@socketio.on('vote')
def handle_vote(data):
    room = session.get('room')
    if room is None:
        print("WS fail to share session.")
        emit_targeted('invalid', {'code':'WS_FAIL_TO_SHARE_SESSION'})
        return
    
    # Check if poll is still open
    room_state = get_room_state(room=room)
    if not room_state == RoomState.POLL_OPENS:
        emit_targeted('invalid', {'code':'POLL_NOT_OPEN'})
        return
    
    # Check if poll token matches
    poll_token = int( r.get(f'room:{room}:current_poll:token') )
    if not data['poll_token'] == poll_token:
        emit_targeted('invalid', {'code':'POLL_TOKEN_MISMATCH'})
        return

    # Check if response is valid
    poll = json.loads( r.get(f'room:{room}:current_poll:content') )
    chosen_option_index = data['option_index']
    if not 0 <= chosen_option_index < len(poll.get('options',[])):
        emit_targeted('invalid', {'code':'POLL_VOTE_OUTSIDE_RANGE'})
        return
    
    # Check if client is editing response and editing is allowed
    poll_id = r.get(f'room:{room}:current_poll:id')
    client_existing_option_index = session.get('votes',{}).get(poll_id)
    response_editable = poll.get('response_editable',False)
    if (client_existing_option_index is not None) and not response_editable:
        emit_targeted('invalid', {'code':'POLL_ALREADY_VOTED'})
        return

    # Process the vote
    if client_existing_option_index is not None:
        # Reverse the previous vote
        r.hincrby(f'room:{room}:votes:{poll_id}', client_existing_option_index, -1)
    # Add current vote
    r.hincrby(f'room:{room}:votes:{poll_id}', chosen_option_index, 1)
    session.setdefault('votes',{})[poll_id] = chosen_option_index

    # Broadcast to all
    socketio.emit('update', state_to_json(room=room), room=room)




#########################################################################

'''
Create new room or reset existing room
'''
@app.route('/<room>/admin/new', methods=['GET'])
def admin_reset_room(room):
    if 'client_id' not in session:
        session['client_id'] = str(uuid.uuid4())
    session['room'] = room
    
    r.set(f'room:{room}:state', RoomState.STARTING_SOON.value)
    r.delete(f'room:{room}:current_poll:id')
    r.delete(f'room:{room}:current_poll:token')
    r.delete(f'room:{room}:current_poll:content')
    # r.delete(f'room:{room}:votes:{poll_id}') # hset
    load_question_bank(room=room)

    return jsonify({'success': True, 'message': 'Room created'})


def load_question_bank(room):
    # Remove old question bank
    r.delete(f'room:{room}:polls')

    # Load question bank
    from polls import polls
    for poll_id, poll_content in polls.items():
        # Serialise poll content
        r.hset(f'room:{room}:polls', poll_id, json.dumps(poll_content))

        # Prepare memory for votes
        options_len = len(poll_content.get('options'))
        for i in range(options_len):
            r.hset(f'room:{room}:votes:{poll_id}', i, 0)


'''
Start accepting poll responses
'''
@app.route('/<room>/admin/open/<poll_id>', methods=['GET'])
def poll_open(room, poll_id):
    # Check that poll_id exists (question exists in question bank)
    serialised_content = r.hget(f'room:{room}:polls', poll_id)
    if serialised_content is None:
        return jsonify({'success': False, 'message': 'poll_id not found'})
    
    # Load question from question bank
    r.set(f'room:{room}:state', RoomState.POLL_OPENS.value)
    r.set(f'room:{room}:current_poll:id', poll_id)
    r.set(f'room:{room}:current_poll:token', random.randint(100000, 999999))
    r.set(f'room:{room}:current_poll:content', serialised_content)

    # Broadcast to all
    socketio.emit('update', state_to_json(room=room), room=room)

    return jsonify({'success': True, 'message': 'Poll started'})


'''
Stop accepting poll responses, answer not revealed yet.
This state could be skipped, from POLL_OPENS to POLL_ANSWER
'''
@app.route('/<room>/admin/close/<poll_id>', methods=['GET'])
def poll_close(room, poll_id):
    # Check that poll_id exists (question exists in question bank)
    serialised_content = r.hget(f'room:{room}:polls', poll_id)
    if serialised_content is None:
        return jsonify({'success': False, 'message': 'poll_id not found'})
    
    # Load question from question bank
    r.set(f'room:{room}:state', RoomState.POLL_CLOSES.value)
    r.set(f'room:{room}:current_poll:id', poll_id)
    r.delete(f'room:{room}:current_poll:token')
    r.set(f'room:{room}:current_poll:content', serialised_content)

    # Broadcast to all
    socketio.emit('update', state_to_json(room=room), room=room)

    return jsonify({'success': True, 'message': 'Poll stopped'})


'''
Stop accepting poll responses and reveal answer
'''
@app.route('/<room>/admin/reveal/<poll_id>', methods=['GET'])
def poll_result(room, poll_id):
    # Check that poll_id exists (question exists in question bank)
    serialised_content = r.hget(f'room:{room}:polls', poll_id)
    if serialised_content is None:
        return jsonify({'success': False, 'message': 'poll_id not found'})
    
    # Load question from question bank
    r.set(f'room:{room}:state', RoomState.POLL_ANSWER.value)
    r.set(f'room:{room}:current_poll:id', poll_id)
    r.delete(f'room:{room}:current_poll:token')
    r.set(f'room:{room}:current_poll:content', serialised_content)

    # Broadcast to all
    socketio.emit('update', state_to_json(room=room), room=room)

    return jsonify({'success': True, 'message': 'Answer revealed'})


        


############################################################################

def get_room_state(room):
    retrieved_room_state = r.get(f'room:{room}:state')
    if retrieved_room_state is None:
        return RoomState.NOT_RUNNING
    return RoomState( int(retrieved_room_state) )

'''
Convert current poll state to json to be broadcasted to clients
'''
def state_to_json(room) -> dict:
    room_state = get_room_state(room=room)
    
    match room_state:
        case RoomState.NOT_RUNNING | RoomState.STARTING_SOON:
            # Nothing to show
            return {'state': room_state.name}
        case RoomState.POLL_OPENS | RoomState.POLL_CLOSES:
            # Show question/options and question/votes
            poll = json.loads( r.get(f'room:{room}:current_poll:content') )
            poll_id = r.get(f'room:{room}:current_poll:id')
            _,anonymised_votes = anonymise_optionsVotes(room=room)
            return {
                'state': room_state.name,
                'poll_token': int( r.get(f'room:{room}:current_poll:token') ),
                'question': poll.get('question'),
                'options': poll.get('options'),
                'response_editable': poll.get('response_editable'),
                'anonymised_votes': anonymised_votes
            }
        case RoomState.POLL_ANSWER:
            # Show question/options+votes/answer
            poll = json.loads( r.get(f'room:{room}:current_poll:content') )
            # poll_id = r.get(f'room:{room}:current_poll:id')
            anonymised_options,anonymised_votes = anonymise_optionsVotes(room=room)
            return {
                'state': room_state.name,
                'question': poll.get('question'),
                'options': poll.get('options'),
                'correct_answer': poll.get('correct_answer'),
                'anonymised_votes': anonymised_votes,
                'anonymised_options': anonymised_options
            }


'''
Anonymise the poll votes using a seed based on the poll_id
'''
def anonymise_optionsVotes(room) -> Tuple[list,list]:
    # Get current poll
    poll_id = r.get(f'room:{room}:current_poll:id')
    poll = json.loads( r.get(f'room:{room}:current_poll:content') )

    # Original lists to be shuffled
    options = poll.get('options')
    votes = list( r.hgetall(f'room:{room}:votes:{poll_id}').values() )

    # Use a seed derived from the poll_id for predictable shuffling
    seed = hash(poll_id) & 0xffffffff
    random.seed(seed)
    shuffle_idx = list( range(len(votes)) )
    random.shuffle(shuffle_idx)
    
    # Shuffle
    shuffled_options = [options[i] for i in shuffle_idx]
    shuffled_votes = [votes[i] for i in shuffle_idx]

    return shuffled_options, shuffled_votes


'''
Convert client vote to json to send back to the client
'''
def clientvote_to_json(room) -> dict:
    poll_id = r.get(f'room:{room}:current_poll:id')
    poll_token = int( r.get(f'room:{room}:current_poll:token') )

    # Exiting vote
    client_existing_option_index = session.get('votes',{}).get(poll_id,-1)

    return {
        'voted': False if client_existing_option_index==-1 else True,
        'myvote': client_existing_option_index,
        'voted_poll_token': poll_token
    }



if __name__ == '__main__':
    socketio.run(app, debug=True)
