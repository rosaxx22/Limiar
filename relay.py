"""LIMIAR relay v1. Python 3.11+, standard library only. TLS terminates at Caddy.
Room membership and reconnection are server-owned; game simulation is host-owned.
No microphone audio, session tokens or room codes are written to disk/logs.
"""
import asyncio, base64, hashlib, json, math, os, secrets, struct, time
from collections import defaultdict, deque

VERSION = 1
ROOMS = {}
CLIENTS = set()
ATTEMPTS = defaultdict(deque)
GRACE = 90
MAX_ROOMS = int(os.environ.get('MAX_ROOMS', '200'))

class Peer:
    def __init__(self, reader, writer):
        self.reader, self.writer = reader, writer
        self.room, self.id = None, 0
        self.queue = asyncio.Queue(128)
        self.count, self.window = 0, time.monotonic()

    def send(self, data, binary=False):
        payload = data if binary else json.dumps(data, separators=(',', ':'), ensure_ascii=False).encode()
        if self.queue.full():
            self.writer.close()  # Bound latency and memory for a slow receiver.
            return
        self.queue.put_nowait((2 if binary else 1, payload))

    async def output(self):
        while True:
            opcode, data = await self.queue.get()
            header = bytes([0x80 | opcode])
            header += bytes([len(data)]) if len(data) < 126 else b'\x7e' + struct.pack('!H', len(data))
            self.writer.write(header + data)
            await asyncio.wait_for(self.writer.drain(), 5)

    def error(self, text): self.send({'t':'error', 'message':text})

def broadcast(room, data, exclude=None, binary=False):
    for member in room['members'].values():
        peer = member['peer']
        if peer and peer is not exclude: peer.send(data, binary)

def voice_recipients(room, sender):
    state=(room.get('state') or {}).get('data',{})
    poses=state.get('poses',{})
    down=state.get('down',{})
    source=poses.get(str(sender),{}).get('p')
    if not isinstance(source,list) or len(source)!=3: return []
    result=[]
    for pid,member in room['members'].items():
        target=poses.get(str(pid),{}).get('p')
        if pid==sender or not member['peer'] or not isinstance(target,list) or len(target)!=3: continue
        if bool(down.get(str(pid))) != bool(down.get(str(sender))): continue
        try:
            distance=sum((float(a)-float(b))**2 for a,b in zip(source,target))
            if math.isfinite(distance) and distance<=28**2: result.append(member['peer'])
        except (ValueError,TypeError,OverflowError): continue
    return result

def roster(room):
    return {'t':'roster', 'code':room['code'], 'started':bool(room['start']),
            'members':[{'id':i, 'name':m['name'], 'ready':m['ready'], 'online':m['peer'] is not None}
                       for i,m in room['members'].items()]}

def attach(peer, room, pid, name, token=None):
    token = token or secrets.token_urlsafe(32)
    room['members'][pid] = {'name':name, 'token':token, 'peer':peer, 'ready':pid==1, 'gone':0}
    peer.room, peer.id = room, pid
    peer.send({'t':'welcome', 'id':pid, 'code':room['code'], 'token':token})
    broadcast(room, roster(room))

def leave(peer, permanent=False):
    room = peer.room
    if not room: return
    member = room['members'].get(peer.id)
    if not member or member['peer'] is not peer: return
    member['peer'], member['gone'] = None, time.monotonic()
    if permanent:
        if peer.id == 1:
            broadcast(room, {'t':'ended','message':'O anfitrião encerrou a sala.'})
            ROOMS.pop(room['code'], None)
        else: del room['members'][peer.id]
    broadcast(room, roster(room))
    peer.room = None

def handle(peer, message):
    if not isinstance(message, dict): raise ValueError('object required')
    t = message.get('t')
    if t in ('state','pose','action','loaded','event') and not isinstance(message.get('data'),dict): return
    if t == 'ping': peer.send({'t':'pong','stamp':message.get('stamp',0)}); return
    if t in ('create','join','resume'):
        if peer.room: peer.error('Você já está em uma sala.'); return
        if message.get('version') != VERSION: peer.error('Versão incompatível. Atualize o jogo.'); return
        name = ''.join(c for c in str(message.get('name','Expedicionário')) if c.isprintable())[:20].strip() or 'Expedicionário'
        if t == 'create':
            if len(ROOMS) >= MAX_ROOMS: peer.error('Servidor cheio. Tente mais tarde.'); return
            alphabet = 'ABCDEFGHJKLMNPQRSTUVWXYZ'
            code = ''.join(secrets.choice(alphabet) for _ in range(3)) + '-' + ''.join(secrets.choice('0123456789') for _ in range(4))
            while code in ROOMS: code = ''.join(secrets.choice(alphabet) for _ in range(3))+'-'+str(secrets.randbelow(9000)+1000)
            room = {'code':code, 'members':{}, 'start':None, 'state':None, 'born':time.monotonic()}
            ROOMS[code] = room
            attach(peer, room, 1, name)
        else:
            room = ROOMS.get(str(message.get('code','')).upper().strip())
            if not room: peer.error('Sala não encontrada ou encerrada.'); return
            if t == 'resume':
                token = str(message.get('token',''))
                match = next(((i,m) for i,m in room['members'].items() if secrets.compare_digest(m['token'],token)),None)
                if not match: peer.error('A reserva de reconexão expirou.'); return
                pid, member = match
                if member['peer']: member['peer'].writer.close()
                attach(peer,room,pid,member['name'],token)
                if room['start']: peer.send(room['start'])
                if room['state']: peer.send(room['state'])
            else:
                if room['start']: peer.error('Partida em andamento. Só é possível reconectar.'); return
                if len(room['members']) >= 8: peer.error('Sala cheia: máximo de 8 jogadores.'); return
                attach(peer, room, next(i for i in range(2,9) if i not in room['members']), name)
        return
    room = peer.room
    if not room or room['code'] not in ROOMS: return
    if t == 'leave': leave(peer,True); return
    if t == 'ready' and not room['start']:
        room['members'][peer.id]['ready'] = bool(message.get('value'))
        broadcast(room,roster(room)); return
    if t == 'start' and peer.id == 1:
        chapter = message.get('chapter',1)
        if chapter not in (1,2,3): return
        if not room['start'] and any(not m['ready'] or not m['peer'] for m in room['members'].values()):
            peer.error('Espere todos estarem conectados e prontos.'); return
        team = room['start']['team'] if room['start'] else len(room['members'])
        room['start'] = {'t':'start','chapter':chapter,'team':team,'seed':secrets.randbelow(2**30), 'continuation':bool(room['start'])}
        room['state'] = None
        broadcast(room,room['start']); return
    if t == 'state' and peer.id == 1 and room['start']:
        room['state'] = {'t':'state','data':message.get('data',{}),'from':1}
        broadcast(room,room['state'],peer); return
    if t in ('pose','action','loaded') and room['start']:
        host = room['members'][1]['peer']
        if host: host.send({'t':t,'data':message.get('data',{}),'from':peer.id})
        return
    if t == 'event' and peer.id == 1 and room['start']:
        target = int(message.get('to',0))
        packet = {'t':'event','data':message.get('data',{}),'from':1}
        if target:
            recipient = room['members'].get(target,{}).get('peer')
            if recipient: recipient.send(packet)
        else: broadcast(room,packet)

async def connection(reader, writer):
    peer, output = Peer(reader,writer), None
    ip = writer.get_extra_info('peername')[0]
    now = time.monotonic()
    attempts = ATTEMPTS[ip]
    while attempts and now-attempts[0] > 60: attempts.popleft()
    # A reverse proxy can make many real users share one source address.
    if len(attempts) >= 240 or len(CLIENTS) >= MAX_ROOMS*8+40:
        writer.close(); return
    attempts.append(now)
    CLIENTS.add(peer)
    try:
        request = (await asyncio.wait_for(reader.readuntil(b'\r\n\r\n'),8)).decode('ascii')
        if len(request)>8192: return
        lines = request.split('\r\n')
        if lines[0].startswith('GET /health '):
            writer.write(b'HTTP/1.1 200 OK\r\nContent-Length: 2\r\nConnection: close\r\n\r\nOK'); await writer.drain(); return
        headers = dict(line.lower().split(':',1) for line in lines[1:] if ':' in line)
        # Header values are case-sensitive (the WebSocket nonce in particular).
        headers = {line.split(':',1)[0].lower():line.split(':',1)[1].strip() for line in lines[1:] if ':' in line}
        if headers.get('upgrade','').lower() != 'websocket' or headers.get('sec-websocket-version') != '13': return
        key = headers['sec-websocket-key']
        if len(base64.b64decode(key,validate=True)) != 16: return
        accept = base64.b64encode(hashlib.sha1((key+'258EAFA5-E914-47DA-95CA-C5AB0DC85B11').encode()).digest())
        writer.write(b'HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Accept: '+accept+b'\r\n\r\n')
        await writer.drain()
        output = asyncio.create_task(peer.output())
        while True:
            a,b = await asyncio.wait_for(reader.readexactly(2),35)
            opcode, length = a&15, b&127
            if not a&128 or a&112 or not b&128: break
            if length == 126: length = struct.unpack('!H',await reader.readexactly(2))[0]
            elif length == 127: break
            if length > 60000 or (opcode>=8 and length>125): break
            mask = await reader.readexactly(4)
            raw = await reader.readexactly(length)
            data = bytes(v^mask[i%4] for i,v in enumerate(raw))
            now = time.monotonic()
            if now-peer.window > 1: peer.window,peer.count = now,0
            peer.count += 1
            if peer.count > 100: break
            if opcode == 8: break
            if opcode == 9: peer.queue.put_nowait((10,data)); continue
            if opcode == 10: continue
            if opcode == 1: handle(peer,json.loads(data))
            elif opcode == 2 and peer.room and peer.room['start'] and 0 < len(data) <= 1282:
                # Source ID is injected here, never accepted from the sending client.
                for recipient in voice_recipients(peer.room,peer.id): recipient.send(bytes([peer.id])+data,True)
    except (asyncio.TimeoutError, asyncio.IncompleteReadError, ValueError, KeyError, ConnectionError, asyncio.LimitOverrunError, asyncio.QueueFull):
        pass
    finally:
        leave(peer)
        CLIENTS.discard(peer)
        if output:
            output.cancel()
            try: await output
            except (asyncio.CancelledError,ConnectionError,asyncio.TimeoutError): pass
        writer.close()

async def cleanup():
    while True:
        await asyncio.sleep(5)
        now=time.monotonic()
        for code,room in list(ROOMS.items()):
            host=room['members'].get(1)
            if not host or (not host['peer'] and now-host['gone']>GRACE):
                broadcast(room,{'t':'ended','message':'O anfitrião não reconectou em 90 segundos.'})
                ROOMS.pop(code,None); continue
            for pid,m in list(room['members'].items()):
                if pid!=1 and not m['peer'] and now-m['gone']>GRACE:
                    del room['members'][pid]; broadcast(room,roster(room))
        for ip,q in list(ATTEMPTS.items()):
            if not q or now-q[-1]>60: ATTEMPTS.pop(ip,None)

async def main():
    server=await asyncio.start_server(connection,os.environ.get('BIND','0.0.0.0'),int(os.environ.get('PORT','8747')),limit=65536)
    print('LIMIAR relay ready',flush=True)
    async with server: await asyncio.gather(server.serve_forever(),cleanup())

if __name__=='__main__': asyncio.run(main())
