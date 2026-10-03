"""Protocol/integration tests against a running relay. No third-party packages."""
import asyncio, base64, json, os, struct

class Client:
    async def connect(self):
        self.reader,self.writer=await asyncio.open_connection('127.0.0.1',8747)
        nonce=base64.b64encode(os.urandom(16)).decode()
        self.writer.write(('GET / HTTP/1.1\r\nHost: localhost\r\nUpgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Key: '+nonce+'\r\nSec-WebSocket-Version: 13\r\n\r\n').encode())
        reply=await self.reader.readuntil(b'\r\n\r\n')
        assert b'101 Switching' in reply
        return self
    async def send(self,data,binary=False):
        raw=data if binary else json.dumps(data).encode()
        mask=os.urandom(4)
        header=bytes([0x82 if binary else 0x81])+(bytes([0x80|len(raw)]) if len(raw)<126 else b'\xfe'+struct.pack('!H',len(raw)))
        self.writer.write(header+mask+bytes(x^mask[i%4] for i,x in enumerate(raw))); await self.writer.drain()
    async def read(self):
        a,b=await asyncio.wait_for(self.reader.readexactly(2),3)
        n=b&127
        if n==126:n=struct.unpack('!H',await self.reader.readexactly(2))[0]
        raw=await self.reader.readexactly(n)
        return raw if a&15==2 else json.loads(raw)
    async def until(self,kind):
        for _ in range(50):
            value=await self.read()
            if isinstance(value,dict) and value.get('t')==kind:return value
        raise AssertionError(kind)
    async def close(self): self.writer.close(); await self.writer.wait_closed()

async def main():
    clients=[]; count=0
    async def new():
        c=await Client().connect(); clients.append(c); return c
    host=await new(); await host.send({'t':'create','version':1,'name':'HOST'})
    welcome=await host.until('welcome'); code=welcome['code']; token=welcome['token']
    assert len(code)==8 and code[3]=='-'; count+=1
    peers=[]
    for i in range(7):
        c=await new(); await c.send({'t':'join','version':1,'name':f'P{i+2}','code':code})
        data=await c.until('welcome'); assert data['id']==i+2; count+=1
        peers.append((c,data)); await c.send({'t':'ready','value':True})
    ninth=await new(); await ninth.send({'t':'join','version':1,'code':code})
    assert '8' in (await ninth.until('error'))['message']; count+=1
    outsider=await new(); await outsider.send({'t':'create','version':1})
    other=await outsider.until('welcome'); assert other['code']!=code; count+=1
    await asyncio.sleep(.1)
    await host.send({'t':'start','chapter':1})
    for c in [host]+[p[0] for p in peers]:
        started=await c.until('start'); assert started['team']==8 and started['chapter']==1; count+=1
    await peers[0][0].send({'t':'pose','from':1,'data':{'p':[1,0,3]}})
    pose=await host.until('pose'); assert pose['from']==2; count+=1
    await host.send({'t':'state','data':{'g':['generator:1'],'poses':{'1':{'p':[0,0,0]},'2':{'p':[1,0,0]},'3':{'p':[100,0,0]}}}})
    for c,data in peers:
        assert (await c.until('state'))['data']['g']==['generator:1']; count+=1
    await peers[0][0].send(b'\x01\x00'+bytes(1280),True)
    voice=await host.read(); assert isinstance(voice,bytes) and voice[0]==2 and len(voice)==1283; count+=1
    try: await asyncio.wait_for(peers[1][0].read(),.2); raise AssertionError('Voice crossed proximity boundary')
    except asyncio.TimeoutError: count+=1
    await peers[0][0].send({'t':'state','data':{'hacked':True}})
    await peers[0][0].send({'t':'event','data':{'hacked':True}})
    try: await asyncio.wait_for(host.read(),.2); raise AssertionError('Client impersonated host')
    except asyncio.TimeoutError: count+=1
    await outsider.until('roster')
    try: await asyncio.wait_for(outsider.read(),.2); raise AssertionError('Room data leaked to another room')
    except asyncio.TimeoutError: count+=1
    # Retained identity/state on reconnect, no duplicate slot, original secret required.
    old,old_data=peers[0]; await old.close(); await asyncio.sleep(.1)
    resumed=await new(); await resumed.send({'t':'resume','version':1,'code':code,'token':old_data['token']})
    assert (await resumed.until('welcome'))['id']==2; count+=1
    assert (await resumed.until('start'))['team']==8; count+=1
    assert (await resumed.until('state'))['data']['g']==['generator:1']; count+=1
    invalid=await new(); await invalid.send({'t':'resume','version':1,'code':code,'token':'invalid'})
    assert 'expirou' in (await invalid.until('error'))['message']; count+=1
    await invalid.send({'t':'join','version':1,'code':code})
    assert 'andamento' in (await invalid.until('error'))['message']; count+=1
    await invalid.send({'t':'join','version':999,'code':code})
    assert 'incompat' in (await invalid.until('error'))['message']; count+=1
    # Host reconnect retains simulation authority and room state.
    await host.close(); await asyncio.sleep(.1)
    replacement=await new(); await replacement.send({'t':'resume','version':1,'code':code,'token':token})
    assert (await replacement.until('welcome'))['id']==1; count+=1
    await replacement.until('start'); await replacement.until('state')
    await replacement.send({'t':'event','to':2,'data':{'kind':'hint','text':'private'}})
    assert (await resumed.until('event'))['data']['text']=='private'; count+=1
    await replacement.send({'t':'start','chapter':2})
    assert (await resumed.until('start'))['chapter']==2; count+=1
    await replacement.send({'t':'leave'})
    assert (await resumed.until('ended'))['message']; count+=1
    for c in clients:
        if not c.writer.is_closing(): await c.close()
    print(f'RELAY_RESULT checks={count} failures=0')

if __name__=='__main__': asyncio.run(main())
