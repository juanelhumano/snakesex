from flask import Flask, request
from flask_socketio import SocketIO, emit, join_room, leave_room
import random
import string
import eventlet

eventlet.monkey_patch()

app = Flask(__name__)
socketio = SocketIO(app, cors_allowed_origins="*")

rooms = {}

# Tamaño del mapa (40x40 bloques)
GRID_SIZE = 40

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
        # Mantenemos 5 comidas a la vez
        'foods': [{'x': random.randint(0, GRID_SIZE-1), 'y': random.randint(0, GRID_SIZE-1)} for _ in range(5)],
        # Generamos 15 obstáculos metálicos fijos
        'obstacles': [{'x': random.randint(2, GRID_SIZE-3), 'y': random.randint(2, GRID_SIZE-3)} for _ in range(15)],
        'rankings': []
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
        # Posición inicial aleatoria para invitados
        start_y = random.randint(5, GRID_SIZE-10)
        start_x = random.randint(5, GRID_SIZE-10)
        rooms[room]['players'][request.sid] = {
            'nick': nick,
            'body': [{'x': start_x, 'y': start_y}, {'x': start_x-1, 'y': start_y}, {'x': start_x-2, 'y': start_y}, {'x': start_x-3, 'y': start_y}],
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
            
            # --- NUEVA LÓGICA: ATRAVESAR PAREDES ---
            # Si sale de la cuadrícula (0-39), vuelve a entrar por el lado opuesto
            head['x'] = head['x'] % GRID_SIZE
            head['y'] = head['y'] % GRID_SIZE
            
            died = False
            
            # Ya no hay colisión con paredes.
                
            # 1. Colisión con obstáculos METÁLICOS
            for obs in game_state['obstacles']:
                if obs['x'] == head['x'] and obs['y'] == head['y']:
                    died = True
                    break

            # 2. Colisión con el cuerpo de otros o de sí mismo
            if not died:
                for other_sid, other_player in game_state['players'].items():
                    if not other_player['is_alive']: continue
                    # Comprobamos cada parte del cuerpo
                    for index, part in enumerate(other_player['body']):
                        # Si es la cabeza de otra serpiente y chocan cabezas, ambos mueren. 
                        # Si choca con el cuerpo (index > 0), muere el que choca.
                        if head['x'] == part['x'] and head['y'] == part['y']:
                            died = True
                            break
            
            if died:
                player['is_alive'] = False
                game_state['rankings'].append({'nick': player['nick'], 'score': player['score']})
                continue

            # Mover la serpiente insertando nueva cabeza
            player['body'].insert(0, head)

            # Comer (Iteramos sobre las 5 comidas)
            ate = False
            for i, food in enumerate(game_state['foods']):
                if head['x'] == food['x'] and head['y'] == food['y']:
                    player['score'] += 10 # Sumamos puntos
                    # Reaparecer comida en lugar aleatorio
                    game_state['foods'][i] = {'x': random.randint(0, GRID_SIZE-1), 'y': random.randint(0, GRID_SIZE-1)}
                    ate = True
                    break
            
            if not ate:
                # Si no comió, removemos la cola para mantener el tamaño actual
                player['body'].pop()

        # --- LÓGICA DE FIN DE JUEGO (Tabla de posiciones) ---
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

        # Enviar estado actualizado del juego a todos en la sala
        socketio.emit('game_state', game_state, to=room)
        # Velocidad del juego (10 FPS)
        socketio.sleep(0.1)

if __name__ == '__main__':
    # Usar gunicorn para producción, pero localmente Flask-SocketIO está bien
    socketio.run(app, host='0.0.0.0', port=5000, debug=True)
