from flask import Flask, jsonify, session, render_template
from flask_socketio import SocketIO, join_room
from flask_session import Session # enable server side session management
from flask_socketio import emit as emit_targeted # this is context sensitive emit
from flask_cors import CORS
from typing import Tuple

import random
import uuid

from redis_interface import *
from roomstate import RoomState




# Set up Flask app
app = Flask(__name__)
app.config['SECRET_KEY'] = 'aertf7896234987fsdhudsf&*TY@WEUIS!'
app.config['CORS_HEADERS'] = 'Content-Type'
cors = CORS(app, resources={r"/*": {"origins": "*"}})
# Configure Redis for storing the session data on the server-side
app.config['SESSION_TYPE'] = 'redis'
app.config['SESSION_PERMANENT'] = False
app.config['SESSION_USE_SIGNER'] = True
app.config['SESSION_REDIS'] = session_db()
Session(app)
socketio = SocketIO(app, cors_allowed_origins="*", manage_session=False)






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
    # Create session (shared with websocket)
    if not session.get('room') == room:
        session['room'] = room
        session.modified = True  # Mutable datatype not automatically detected
        print(f"[{room}] New visitor")
    else:
        print(f"[{room}] Returning visitor")
    
    return render_template('index.html')


'''
Client connects to websocket
'''
@socketio.on('connect')
def handle_connect():
    # Expect to get session from HTML Flask session
    room = session.get('room')
    if room is None:
        print("WS fail to share session.")
        emit_targeted('invalid', {'code':'WS_FAIL_TO_SHARE_SESSION'})
        return

    # Join room
    join_room(room)
    print(f"[{room}] WebSocket")

    # Client context aware emit
    emit_targeted('myvote', clientvote_to_json(room=room))      # Votes placed by client (emit first)
    emit_targeted('update', state_to_json(room=room))           # Overall state of poll  (emit last)


'''
Client requests for state
'''
@socketio.on('getstate')
def handle_getstate():
    # Expect to get session from HTML Flask session
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
    # Expect to get session from HTML Flask session
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
    poll_token = get_current_poll_token(room=room)
    if not data['poll_token'] == poll_token:
        emit_targeted('invalid', {'code':'POLL_TOKEN_MISMATCH'})
        return

    # Check if response is valid
    poll = get_current_poll(room=room)
    chosen_option_index = data['option_index']
    if not 0 <= chosen_option_index < len(poll.get('options',[])):
        emit_targeted('invalid', {'code':'POLL_VOTE_OUTSIDE_RANGE'})
        return
    
    # Check if client is editing response and editing is allowed
    poll_id = get_current_poll_id(room=room)
    session.setdefault('votes',{}).setdefault(poll_id,{})
    client_existing_poll_token = session['votes'][poll_id].get('token')
    client_existing_option_index = session['votes'][poll_id].get('option')
    if client_existing_poll_token == poll_token and client_existing_option_index is not None:
        # Same poll was already voted by client
        if not poll.get('response_editable',False):
            # Response cannot be changed
            emit_targeted('invalid', {'code':'POLL_ALREADY_VOTED'})
            return
        # Reverse previous vote
        vote_increment(room=room, poll_id=poll_id, option_index=client_existing_option_index, value_incr=-1)

    # Add current vote
    vote_increment(room=room, poll_id=poll_id, option_index=chosen_option_index, value_incr=1)
    session['votes'][poll_id]['option'] = chosen_option_index
    session['votes'][poll_id]['token'] = poll_token
    session.modified = True  # Mutable datatype not automatically detected

    emit_targeted('myvote', clientvote_to_json(room=room))      # Votes placed by client (emit first)
    # Broadcast to all (lightweight status update)
    socketio.emit('vote_update', vote_state_to_json(room=room), room=room)




#########################################################################

'''
Create new room or reset existing room
'''
@app.route('/<room>/admin/new', methods=['GET'])
def admin_reset_room(room):
    #TODO Verify/create admin status
    session['room'] = room
    session.modified = True  # Mutable datatype not automatically detected
    
    delete_room_keys(room=room)
    set_room_state(room=room, state=RoomState.STARTING_SOON)
    load_question_bank(room=room)

    # Broadcast to all
    socketio.emit('update', state_to_json(room=room), room=room)
    return jsonify({'success': True, 'message': 'Room created'})


'''
Close room (data is retained)
'''
@app.route('/<room>/admin/close', methods=['GET'])
def admin_close_room(room):
    #TODO Verify admin status
    set_room_state(room=room, state=RoomState.NOT_RUNNING)

    # Broadcast to all
    socketio.emit('update', state_to_json(room=room), room=room)
    return jsonify({'success': True, 'message': 'Room closed'})


'''
Delete room (data is deleted)
'''
@app.route('/<room>/admin/delete', methods=['GET'])
def admin_delete_room(room):
    #TODO Verify admin status
    delete_room_keys(room=room)

    # Broadcast to all
    socketio.emit('update', state_to_json(room=room), room=room)
    return jsonify({'success': True, 'message': 'Room deleted'})


'''
Start accepting poll responses
'''
@app.route('/<room>/admin/open/<poll_id>', methods=['GET'])
def poll_open(room, poll_id):
    #TODO Verify admin status
    # Set current state
    if not set_current_poll_state(room=room, poll_id=poll_id):
        return jsonify({'success': False, 'message': 'Poll_id may be incorrect.'})
    if not set_room_state(room=room, state=RoomState.POLL_OPENS):
        return jsonify({'success': False, 'message': 'Cannot set room state.'})

    # Broadcast to all
    socketio.emit('update', state_to_json(room=room), room=room)
    return jsonify({'success': True, 'message': 'Poll started'})


'''
Reset poll responses
'''
@app.route('/<room>/admin/reset/<poll_id>', methods=['GET'])
def poll_reset(room, poll_id):
    #TODO Verify admin status
    # Set current state
    if not set_current_poll_state(room=room, poll_id=poll_id):
        return jsonify({'success': False, 'message': 'Poll_id may be incorrect.'})
    if not set_room_state(room=room, state=RoomState.POLL_OPENS):
        return jsonify({'success': False, 'message': 'Cannot set room state.'})

    reset_poll(room=room, poll_id=poll_id)

    # Broadcast to all
    socketio.emit('update', state_to_json(room=room), room=room)
    return jsonify({'success': True, 'message': 'Poll reset'})


'''
Stop accepting poll responses, answer not revealed yet.
This state could be skipped, from POLL_OPENS to POLL_ANSWER
'''
@app.route('/<room>/admin/close/<poll_id>', methods=['GET'])
def poll_close(room, poll_id):
    #TODO Verify admin status
    # Set current state
    if not set_current_poll_state(room=room, poll_id=poll_id):
        return jsonify({'success': False, 'message': 'Poll_id may be incorrect.'})
    if not set_room_state(room=room, state=RoomState.POLL_CLOSES):
        return jsonify({'success': False, 'message': 'Cannot set room state.'})

    # Broadcast to all
    socketio.emit('update', state_to_json(room=room), room=room)
    return jsonify({'success': True, 'message': 'Poll stopped'})


'''
Stop accepting poll responses and reveal answer
'''
@app.route('/<room>/admin/reveal/<poll_id>', methods=['GET'])
def poll_result(room, poll_id):
    #TODO Verify admin status
    # Set current state
    if not set_current_poll_state(room=room, poll_id=poll_id):
        return jsonify({'success': False, 'message': 'Poll_id may be incorrect.'})
    if not set_room_state(room=room, state=RoomState.POLL_ANSWER):
        return jsonify({'success': False, 'message': 'Cannot set room state.'})

    # Broadcast to all
    socketio.emit('update', state_to_json(room=room), room=room)
    return jsonify({'success': True, 'message': 'Answer revealed'})


        


############################################################################



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
            poll = get_current_poll(room=room)
            # poll_id = get_current_poll_id(room=room)
            _,anonymised_votes = anonymise_optionsVotes(room=room)
            return {
                'state': room_state.name,
                'poll_token': get_current_poll_token(room=room),
                'question': poll.get('question'),
                'figure_mime': poll.get('figure_mime'),
                'figure_base64': poll.get('figure_base64'),
                'options': poll.get('options'),
                'response_currently_editable': poll.get('response_editable')==True and room_state == RoomState.POLL_OPENS,
                'anonymised_votes': anonymised_votes
            }
        case RoomState.POLL_ANSWER:
            # Show question/options+votes/answer
            poll = get_current_poll(room=room)
            # poll_id = get_current_poll_id(room=room)
            anonymised_options_idx,anonymised_votes = anonymise_optionsVotes(room=room)
            return {
                'state': room_state.name,
                'question': poll.get('question'),
                'figure_mime': poll.get('figure_mime'),
                'figure_base64': poll.get('figure'),
                'options': poll.get('options'),
                'correct_answer': poll.get('correct_answer'),
                'anonymised_votes': anonymised_votes,
                'anonymised_options_idx': anonymised_options_idx
            }

'''
Convert current vote state to json to be broadcasted to clients
'''
def vote_state_to_json(room) -> dict:
    room_state = get_room_state(room=room)
    if not room_state == RoomState.POLL_OPENS:
        return {
            'poll_token': 0,
            'response_currently_editable': False,
            'anonymised_votes': []
        }
    
    poll = get_current_poll(room=room)
    _,anonymised_votes = anonymise_optionsVotes(room=room)
    return {
        'poll_token': get_current_poll_token(room=room),
        'response_currently_editable': poll.get('response_editable')==True and room_state == RoomState.POLL_OPENS,
        'anonymised_votes': anonymised_votes
    }

'''
Anonymise the poll votes using a seed based on the poll_id.
Raises ValueError if for current poll, len(options) mismatches len(votes).
'''
def anonymise_optionsVotes(room) -> Tuple[list,list]:
    # Get current poll
    poll_id = get_current_poll_id(room=room)
    poll = get_current_poll(room=room)

    # Original lists to be shuffled
    options = poll.get('options')
    votes = get_all_votes(room=room, poll_id=poll_id)
    if len(options) is not len(votes):
        raise ValueError("Options and votes size mismatch.")

    # Use a seed derived from the poll_id for predictable shuffling
    seed = hash(poll_id) & 0xffffffff
    random.seed(seed)
    shuffle_idx = list( range(len(votes)) )
    random.shuffle(shuffle_idx)
    
    # Shuffle
    shuffled_options_idx = shuffle_idx
    shuffled_votes = [votes[i] for i in shuffle_idx]

    return shuffled_options_idx, shuffled_votes


'''
Convert client vote to json to send back to the client (targeted)
'''
def clientvote_to_json(room) -> dict:
    poll_id = get_current_poll_id(room=room)
    poll_token = get_current_poll_token(room=room)

    # Existing vote
    session.setdefault('votes',{}).setdefault(poll_id,{})
    client_existing_poll_token = session['votes'][poll_id].get('token')
    client_existing_option_index = session['votes'][poll_id].get('option')
    if client_existing_poll_token == poll_token and client_existing_option_index is not None:
        myvote = client_existing_option_index
    else:
        myvote = -1

    return {
        'voted': myvote > -1,
        'myvote': myvote,
        'voted_poll_token': poll_token
    }



if __name__ == '__main__':
    socketio.run(app, debug=True)
