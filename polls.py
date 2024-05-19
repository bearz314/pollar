# In-memory storage for polls and votes
polls = {
    'example_poll': {
        'question': 'What is your favorite color?',
        'options': ['Red', 'Blue', 'Green', 'Yellow'],
        'votes': [0, 0, 0, 0],
        'correct_answer': 1,  # Assuming 'Blue' is the correct answer
        'show_results': False,
        'response_editable':True
    },
    'another_poll': {
        'question': 'What is the capital of France?',
        'options': ['Berlin', 'Madrid', 'Paris', 'Lisbon'],
        'votes': [0, 0, 0, 0],
        'correct_answer': 2,  # Assuming 'Paris' is the correct answer
        'show_results': False,
        'response_editable':True
    }
}