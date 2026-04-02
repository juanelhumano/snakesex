from flask import Flask, request
from flask_socketio import SocketIO, emit, join_room, leave_room
import random
import string
import eventlet

eventlet.monkey_patch()

app = Flask(__name__)
socketio = SocketIO(app, cors_allowed_origins="*")

rooms = {}

def generate_room_code():
    return ''.join(random.choices(string.ascii_uppercase + string.digits, k=5))

@socketio.on('create_room')
def on_create_room(data):
    room = generate_room_code()
    join_room(room)
    nick = data.get('nick', 'Host')
    
    rooms[room] = {
        'players': {},
        'host': request.sid,
        'state': 'lobby',
        # Ahora hay múltiples comidas y más obstáculos
        'foods': [{'x': random.randint(0, 39), 'y': random.randint(0, 39)} for _ in range(5)],
        'obstacles': [{'x': random.randint(2, 37), 'y': random.randint(2, 37)} for _ in range(15)],
        'rankings': [] # Aquí guardaremos a los que vayan perdiendo
    }
    
    # Serpiente inicial con tamaño 4
    rooms[room]['players'][request.sid] = {
        'nick': nick,
        'body': [{'x': 10, 'y': 5}, {'x': 9, 'y': 5}, {'x': 8, 'y': 5}, {'x': 7, 'y': 5}],
        'dir': 'right',
        'is_alive': True,
        'color': '#3498db',
        'score': 0
    }
    
    emit('room_created', {'room': room})
    emit('update_lobby', get_lobby_info(room), to=room)

@socketio.on('join_room')
def on_join_room(data):
    room = data.get('room', '').upper()
    nick = data.get('nick', 'Invitado')
    
    if room in rooms and rooms[room]['state'] == 'lobby':
        join_room(room)
        start_y = random.randint(5, 30)
        rooms[room]['players'][request.sid] = {
            'nick': nick,
            'body': [{'x': 10, 'y': start_y}, {'x': 9, 'y': start_y}, {'x': 8, 'y': start_y}, {'x': 7, 'y': start_y}],
            'dir': 'right',
            'is_alive': True,
            'color': f'#{random.randint(0, 0xFFFFFF):06x}',
            'score': 0
        }
        emit('room_joined', {'room': room})
        emit('update_lobby', get_lobby_info(room), to=room)
    else:
        emit('error', {'msg': 'La sala no existe o la partida ya inició.'})

@socketio.on('start_game')
def on_start_game(data):
    room = data['room']
    if room in rooms and rooms[room]['host'] == request.sid:
        rooms[room]['state'] = 'playing'
        emit('game_started', to=room)
        socketio.start_background_task(game_loop, room)

@socketio.on('change_dir')
def on_change_dir(data):
    room = data['room']
    new_dir = data['dir']
    if room in rooms and request.sid in rooms[room]['players']:
        current_dir = rooms[room]['players'][request.sid]['dir']
        opposites = {'up': 'down', 'down': 'up', 'left': 'right', 'right': 'left'}
        if new_dir != opposites.get(current_dir):
            rooms[room]['players'][request.sid]['dir'] = new_dir

def get_lobby_info(room):
    # Devolvemos la lista de nombres para mostrar en el lobby
    return {
        'players': [p['nick'] for p in rooms[room]['players'].values()],
        'host': rooms[room]['host']
    }

def game_loop(room):
    while room in rooms and rooms[room]['state'] == 'playing':
        game_state = rooms[room]
        
        alive_count = 0
        last_alive_sid = None

        for sid, player in game_state['players'].items():
            if not player['is_alive']:
                continue
            
            alive_count += 1
            last_alive_sid = sid

            head = player['body'][0].copy()
            if player['dir'] == 'up': head['y'] -= 1
            if player['dir'] == 'down': head['y'] += 1
            if player['dir'] == 'left': head['x'] -= 1
            if player['dir'] == 'right': head['x'] += 1
            
            died = False
            
            # 1. Colisión con la pared
            if head['x'] < 0 or head['x'] >= 40 or head['y'] < 0 or head['y'] >= 40:
                died = True
                
            # 2. Colisión con obstáculos
            if not died:
                for obs in game_state['obstacles']:
                    if obs['x'] == head['x'] and obs['y'] == head['y']:
                        died = True
                        break

            # 3. Colisión con otros o consigo mismo
            if not died:
                for other_sid, other_player in game_state['players'].items():
                    if not other_player['is_alive']: continue
                    for part in other_player['body']:
                        if head['x'] == part['x'] and head['y'] == part['y']:
                            died = True
                            break
            
            if died:
                player['is_alive'] = False
                game_state['rankings'].append({'nick': player['nick'], 'score': player['score']})
                continue

            player['body'].insert(0, head)

            # Comer (Iteramos sobre la lista de comidas)
            ate = False
            for i, food in enumerate(game_state['foods']):
                if head['x'] == food['x'] and head['y'] == food['y']:
                    player['score'] += 10 # Sumamos puntos
                    game_state['foods'][i] = {'x': random.randint(0, 39), 'y': random.randint(0, 39)}
                    ate = True
                    break
            
            if not ate:
                player['body'].pop()

        # --- LÓGICA DE FIN DE JUEGO ---
        total_players = len(game_state['players'])
        # Termina si hay más de 1 jugador y queda 1 o 0 vivos
        if total_players > 1 and alive_count <= 1:
            rooms[room]['state'] = 'game_over'
            if alive_count == 1:
                winner = game_state['players'][last_alive_sid]
                game_state['rankings'].append({'nick': winner['nick'], 'score': winner['score'], 'winner': True})
            
            game_state['rankings'].reverse() # El último en morir / ganador queda primero
            socketio.emit('game_over', {'rankings': game_state['rankings']}, to=room)
            break
            
        # Si juegas solo para probar, termina cuando mueres
        if total_players == 1 and alive_count == 0:
            rooms[room]['state'] = 'game_over'
            game_state['rankings'].reverse()
            socketio.emit('game_over', {'rankings': game_state['rankings']}, to=room)
            break

        socketio.emit('game_state', game_state, to=room)
        socketio.sleep(0.1)

if __name__ == '__main__':
    socketio.run(app, host='0.0.0.0', port=5000)
