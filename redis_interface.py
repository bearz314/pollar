import json
import redis

from roomstate import RoomState

# Configurations
REDIS_HOSTNAME = '127.0.0.1'
REDIS_PORT = 6379


def session_db() -> redis.Redis:
    return redis.Redis(host=REDIS_HOSTNAME, port=REDIS_PORT, db=0)


# Redis for app data
r = redis.Redis(host=REDIS_HOSTNAME, port=REDIS_PORT, db=1, decode_responses=True)

# Keyspace layout
# * 
# * room:{room}:state - Room state
# * 
# * room:{room}:current_poll:id - Poll_id
# * room:{room}:current_poll:token - Random token
# * room:{room}:current_poll:content - Serialised content of poll dict
# *
# * room:{room}:polls -> hash
# * * {poll_id} - Serialised content of poll dict
# *
# * room:{room}:votes:{poll_id} -> hash
# * * {option_index} - Number of votes for this option


'''
Gets current poll ID for the room.
'''
def get_current_poll_id(room:str) -> str | None:
    return r.get(f'room:{room}:current_poll:id')

'''
Gets current poll token for the room.
'''
def get_current_poll_token(room:str) -> int | None:
    token_str = r.get(f'room:{room}:current_poll:token')
    if token_str is None:
        return None
    return int(token_str)

'''
Gets current poll for the room.
If not found, returns an empty dictionary.
'''
def get_current_poll(room:str) -> dict:
    serialised_poll = r.get(f'room:{room}:current_poll:content')
    if serialised_poll is None:
        return {}
    return json.loads(serialised_poll)

'''
Gets poll for the room.
If not found, returns an empty dictionary.
'''
def get_poll(room:str, poll_id:str) -> dict:
    serialised_poll = r.hget(f'room:{room}:polls', poll_id)
    if serialised_poll is None:
        return {}
    return json.loads(serialised_poll)

'''
Get room state.
If not found, returns RoomState.NOT_RUNNING.
'''
def get_room_state(room) -> RoomState:
    retrieved_room_state = r.get(f'room:{room}:state')
    if retrieved_room_state is None:
        return RoomState.NOT_RUNNING
    return RoomState( int(retrieved_room_state) )

'''
Load question bank into Redis for the room. Also sets up memory for votes.
'''
def load_question_bank(room:str) -> None:
    from polls import polls
    for poll_id, poll_dict in polls.items():
        # Serialise poll content
        r.hset(f'room:{room}:polls', poll_id, json.dumps(poll_dict))

        # Prepare memory for votes
        options_len = len(poll_dict.get('options'))
        for i in range(options_len):
            r.hset(f'room:{room}:votes:{poll_id}', i, 0)

'''
Delete all keys associated to a room
'''
def delete_room_keys(room:str) -> None:
    r.delete(f'room:{room}:state')
    r.delete(f'room:{room}:current_poll:id')
    r.delete(f'room:{room}:current_poll:token')
    r.delete(f'room:{room}:current_poll:content')
    # use the poll_id keys to delete votes
    for poll_id in r.hkeys(f'room:{room}:polls'):
        r.delete(f'room:{room}:votes:{poll_id}')
    r.delete(f'room:{room}:polls')

'''
Set the state of the room.
Returns success bool.
'''
def set_room_state(room:str, state:RoomState) -> bool:
    return r.set(f'room:{room}:state', state.value)

'''
Set the state of the current poll.
Returns success bool.
'''
def set_current_poll_state(room:str, poll_id:str, poll_token:int) -> bool:
    # Check poll_id exists
    serialised_poll = r.hget(f'room:{room}:polls', poll_id)
    if serialised_poll is None:
        return False

    res1 = r.set(f'room:{room}:current_poll:id', poll_id)
    res2 = r.set(f'room:{room}:current_poll:token', poll_token)
    res3 = r.set(f'room:{room}:current_poll:content', serialised_poll)

    return res1 and res2 and res3

'''
Change the vote for an option in a poll in a room.
Returns success bool.
'''
def vote_increment(room:str, poll_id:str, option_index:int, value_incr:int) -> bool:
    return r.hincrby(f'room:{room}:votes:{poll_id}', option_index, value_incr)

'''
Gets all the votes sorted according to option index.
If not found, returns [].
'''
def get_all_votes(room:str, poll_id:str) -> list:
    return list( r.hgetall(f'room:{room}:votes:{poll_id}').values() )
