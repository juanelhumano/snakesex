from flask import Flask, request
from flask_socketio import SocketIO, emit, join_room, leave_room
import random
import string
import time
import eventlet

# eventlet es necesario para WebSockets concurrentes en producción (Render)
eventlet.monkey_patch()

app = Flask(__name__)
# Permitimos CORS para que GitHub Pages pueda conectarse al servidor en Render
socketio = SocketIO(app, cors_allowed_origins="*")

# Diccionario para guardar el estado de cada sala
rooms = {}

def generate_room_code():
    return ''.join(random.choices(string.ascii_uppercase + string.digits, k=5))

@socketio.on('create_room')
def on_create_room():
    room = generate_room_code()
    join_room(room)
    # Inicializamos el estado del juego para esta sala
    rooms[room] = {
        'players': {},
        'host': request.sid,
        'state': 'lobby', # lobby o playing
        'food': {'x': random.randint(0, 39), 'y': random.randint(0, 39)},
        'obstacles': [{'x': 10, 'y': 10}, {'x': 20, 'y': 20}], # Ejemplo de obstáculos fijos
        'speed_boosts': [] # Aquí puedes añadir lógica para que aparezcan temporalmente
    }
    
    rooms[room]['players'][request.sid] = {
        'body': [{'x': 5, 'y': 5}],
        'dir': 'right',
        'is_alive': True,
        'color': '#3498db'
    }
    
    emit('room_created', {'room': room})
    emit('update_lobby', get_lobby_info(room), to=room)

@socketio.on('join_room')
def on_join_room(data):
    room = data['room'].upper()
    if room in rooms and rooms[room]['state'] == 'lobby':
        join_room(room)
        # Añadir nuevo jugador
        rooms[room]['players'][request.sid] = {
            'body': [{'x': random.randint(5, 30), 'y': random.randint(5, 30)}],
            'dir': 'right',
            'is_alive': True,
            'color': f'#{random.randint(0, 0xFFFFFF):06x}' # Color aleatorio
        }
        emit('room_joined', {'room': room})
        emit('update_lobby', get_lobby_info(room), to=room)
    else:
        emit('error', {'msg': 'Sala no existe o el juego ya empezó'})

@socketio.on('start_game')
def on_start_game(data):
    room = data['room']
    if room in rooms and rooms[room]['host'] == request.sid:
        rooms[room]['state'] = 'playing'
        emit('game_started', to=room)
        # Iniciar el bucle del juego para esta sala
        socketio.start_background_task(game_loop, room)

@socketio.on('change_dir')
def on_change_dir(data):
    room = data['room']
    new_dir = data['dir']
    if room in rooms and request.sid in rooms[room]['players']:
        # Evitar que la serpiente se regrese sobre sí misma
        current_dir = rooms[room]['players'][request.sid]['dir']
        opposites = {'up': 'down', 'down': 'up', 'left': 'right', 'right': 'left'}
        if new_dir != opposites.get(current_dir):
            rooms[room]['players'][request.sid]['dir'] = new_dir

def get_lobby_info(room):
    return {'player_count': len(rooms[room]['players']), 'host': rooms[room]['host']}

def game_loop(room):
    # Este bucle actualiza la posición de todos a una velocidad constante (ej. 10 cuadros por segundo)
    while room in rooms and rooms[room]['state'] == 'playing':
        game_state = rooms[room]
        
        for sid, player in game_state['players'].items():
            if not player['is_alive']:
                continue
            
            head = player['body'][0].copy()
            if player['dir'] == 'up': head['y'] -= 1
            if player['dir'] == 'down': head['y'] += 1
            if player['dir'] == 'left': head['x'] -= 1
            if player['dir'] == 'right': head['x'] += 1
            
            # 1. Colisiones con la pared (Mapa de 40x40)
            if head['x'] < 0 or head['x'] >= 40 or head['y'] < 0 or head['y'] >= 40:
                player['is_alive'] = False
                continue
                
            # 2. Colisión con obstáculos fijos
            if any(obs['x'] == head['x'] and obs['y'] == head['y'] for obs in game_state['obstacles']):
                 player['is_alive'] = False
                 continue

            # 3. Colisión con otros jugadores (y consigo mismo)
            collision = False
            for other_sid, other_player in game_state['players'].items():
                if not other_player['is_alive']: continue
                for part in other_player['body']:
                    if head['x'] == part['x'] and head['y'] == part['y']:
                        collision = True
                        break
            if collision:
                player['is_alive'] = False
                continue

            # Mover la serpiente
            player['body'].insert(0, head)

            # Comer el item para crecer
            if head['x'] == game_state['food']['x'] and head['y'] == game_state['food']['y']:
                # Reposicionar comida
                game_state['food'] = {'x': random.randint(0, 39), 'y': random.randint(0, 39)}
            else:
                # Si no comió, removemos la cola para que mantenga su tamaño
                player['body'].pop()

        socketio.emit('game_state', game_state, to=room)
        socketio.sleep(0.1) # Controla la velocidad base del juego (100ms)

if __name__ == '__main__':
    socketio.run(app, host='0.0.0.0', port=5000)
