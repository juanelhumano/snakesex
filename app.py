from flask import Flask, request
from flask_socketio import SocketIO, emit, join_room, leave_room
import random
import string
import eventlet

eventlet.monkey_patch()

app = Flask(__name__)
socketio = SocketIO(app, cors_allowed_origins="*")

rooms = {}

GRID_SIZE = 40
PALETA_COLORES = ['#e84118', '#00a8ff', '#4cd137', '#fbc531', '#9c88ff', '#e1b12c', '#0097e6', '#c23616', '#8c7ae6', '#B33771']

def generate_room_code():
    return ''.join(random.choices(string.ascii_uppercase + string.digits, k=5))

def get_available_color(room):
    if room not in rooms: return PALETA_COLORES[0]
    taken_colors = [p['color'] for p in rooms[room]['players'].values()]
    for color in PALETA_COLORES:
        if color not in taken_colors:
            return color
    return PALETA_COLORES[0] # Por si se acaban los colores

@socketio.on('create_room')
def on_create_room(data):
    room = generate_room_code()
    join_room(room)
    nick = data.get('nick', 'Host')
    
    rooms[room] = {
        'players': {},
        'host': request.sid,
        'state': 'lobby',
        'foods': [{'x': random.randint(0, GRID_SIZE-1), 'y': random.randint(0, GRID_SIZE-1)} for _ in range(5)],
        'obstacles': [{'x': random.randint(2, GRID_SIZE-3), 'y': random.randint(2, GRID_SIZE-3)} for _ in range(15)],
        'rankings': [],
        'speed': 0.1 # Velocidad por defecto (se actualiza al iniciar)
    }
    
    rooms[room]['players'][request.sid] = {
        'nick': nick,
        'body': [{'x': 10, 'y': 5}, {'x': 9, 'y': 5}, {'x': 8, 'y': 5}, {'x': 7, 'y': 5}],
        'dir': 'right',
        'is_alive': True,
        'color': get_available_color(room),
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
        start_y = random.randint(5, GRID_SIZE-10)
        start_x = random.randint(5, GRID_SIZE-10)
        rooms[room]['players'][request.sid] = {
            'nick': nick,
            'body': [{'x': start_x, 'y': start_y}, {'x': start_x-1, 'y': start_y}, {'x': start_x-2, 'y': start_y}, {'x': start_x-3, 'y': start_y}],
            'dir': 'right',
            'is_alive': True,
            'color': get_available_color(room),
            'score': 0
        }
        emit('room_joined', {'room': room})
        emit('update_lobby', get_lobby_info(room), to=room)
    else:
        emit('error', {'msg': 'La sala no existe o la partida ya inició.'})

@socketio.on('choose_color')
def on_choose_color(data):
    room = data['room']
    new_color = data['color']
    if room in rooms and rooms[room]['state'] == 'lobby' and request.sid in rooms[room]['players']:
        taken_colors = [p['color'] for p in rooms[room]['players'].values()]
        if new_color not in taken_colors:
            rooms[room]['players'][request.sid]['color'] = new_color
            emit('update_lobby', get_lobby_info(room), to=room)

@socketio.on('start_game')
def on_start_game(data):
    room = data['room']
    initial_speed = float(data.get('speed', 0.1))
    
    if room in rooms and rooms[room]['host'] == request.sid:
        rooms[room]['state'] = 'playing'
        rooms[room]['speed'] = initial_speed # Asignamos la velocidad elegida
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
    # Ahora enviamos más detalle de cada jugador (incluyendo su color)
    players_data = []
    for p in rooms[room]['players'].values():
        players_data.append({'nick': p['nick'], 'color': p['color']})
        
    return {
        'players': players_data,
        'host': rooms[room]['host'],
        'palette': PALETA_COLORES
    }

def game_loop(room):
    ticks = 0
    while room in rooms and rooms[room]['state'] == 'playing':
        game_state = rooms[room]
        current_speed = game_state.get('speed', 0.1)
        
        alive_count = 0
        last_alive_sid = None

        for sid, player in game_state['players'].items():
            if not player['is_alive']: continue
            alive_count += 1
            last_alive_sid = sid

            head = player['body'][0].copy()
            if player['dir'] == 'up': head['y'] -= 1
            if player['dir'] == 'down': head['y'] += 1
            if player['dir'] == 'left': head['x'] -= 1
            if player['dir'] == 'right': head['x'] += 1
            
            # Atravesar paredes
            head['x'] = head['x'] % GRID_SIZE
            head['y'] = head['y'] % GRID_SIZE
            
            died = False
            
            # Colisión con obstáculos METÁLICOS
            for obs in game_state['obstacles']:
                if obs['x'] == head['x'] and obs['y'] == head['y']:
                    died = True
                    break

            # Colisión con el cuerpo
            if not died:
                for other_sid, other_player in game_state['players'].items():
                    if not other_player['is_alive']: continue
                    for index, part in enumerate(other_player['body']):
                        if head['x'] == part['x'] and head['y'] == part['y']:
                            died = True
                            break
            
            if died:
                player['is_alive'] = False
                game_state['rankings'].append({'nick': player['nick'], 'score': player['score']})
                continue

            player['body'].insert(0, head)

            # Comer
            ate = False
            for i, food in enumerate(game_state['foods']):
                if head['x'] == food['x'] and head['y'] == food['y']:
                    player['score'] += 10
                    game_state['foods'][i] = {'x': random.randint(0, GRID_SIZE-1), 'y': random.randint(0, GRID_SIZE-1)}
                    ate = True
                    break
            
            if not ate:
                player['body'].pop()

        # Fin de juego
        total_players = len(game_state['players'])
        if total_players > 1 and alive_count <= 1:
            rooms[room]['state'] = 'game_over'
            if alive_count == 1:
                winner = game_state['players'][last_alive_sid]
                game_state['rankings'].append({'nick': winner['nick'], 'score': winner['score'], 'winner': True})
            game_state['rankings'].reverse()
            socketio.emit('game_over', {'rankings': game_state['rankings']}, to=room)
            break
            
        if total_players == 1 and alive_count == 0:
            rooms[room]['state'] = 'game_over'
            game_state['rankings'].reverse()
            socketio.emit('game_over', {'rankings': game_state['rankings']}, to=room)
            break

        # --- LÓGICA DE ACELERACIÓN PROGRESIVA ---
        ticks += 1
        # Aumentar la velocidad un 3% cada ~50 ciclos (dependiendo de la velocidad actual)
        if ticks % 50 == 0:
            # Límite máximo de velocidad de 0.04 (muy rápido)
            game_state['speed'] = max(0.04, current_speed * 0.97)

        socketio.emit('game_state', game_state, to=room)
        socketio.sleep(current_speed) # Usa la velocidad dinámica

if __name__ == '__main__':
    socketio.run(app, host='0.0.0.0', port=5000, debug=True)
