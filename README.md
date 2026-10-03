# LIMIAR — servidor de salas e relay

O jogo usa conexões **de saída** para este servidor. Jogadores de casas e redes diferentes clicam em **Criar sala** ou **Entrar na sala**, usando códigos como `AUR-5832`. Eles não digitam IP, não abrem portas no roteador e não precisam estar na mesma rede.

**Este pacote não inclui um serviço já hospedado.** Publique o servidor uma vez e coloque sua URL em `online.cfg`, ao lado do `LIMIAR.exe`, antes de distribuir o jogo. Sem essa etapa, o botão online informa que o servidor ainda não foi publicado; o single-player continua disponível.

## Opção simples: Render

1. Envie somente o conteúdo desta pasta `servidor` para um repositório seu no GitHub/GitLab. Não precisa enviar as texturas nem o projeto Godot.
2. No Render, crie um **Web Service**, conecte esse repositório e selecione **Docker**. Dockerfile: `./Dockerfile`; comando inicial: o definido no Dockerfile.
3. Use **uma instância** e uma modalidade que permaneça ligada para evitar a suspensão do serviço. Configure a verificação de saúde em `/health`. O programa lê a variável `PORT` fornecida pela plataforma.
4. Depois que a implantação estiver saudável, anote o domínio fornecido, por exemplo `limiar-salas.onrender.com`.
5. Distribua o cliente com este `online.cfg`:

```ini
[server]
url="wss://limiar-salas.onrender.com"
```

Troque o exemplo pelo domínio real do seu serviço. Use **wss://**, mantendo a verificação do certificado ligada. Todos recebem o mesmo arquivo; ninguém precisa configurar a rede de casa.

O Render suporta WebSockets e termina o TLS na plataforma. Uma implantação/reinicialização substitui a instância e encerra suas conexões; faça atualizações fora das partidas. Consulte as instruções oficiais: [Docker](https://render.com/docs/docker), [WebSockets](https://render.com/docs/websocket) e [Web Services](https://render.com/docs/web-services). A hospedagem é contratada e administrada por você; nenhum serviço foi comprado ou publicado por este projeto.

## Alternativa: VPS Linux com Docker

Use um servidor Linux público com Docker e Docker Compose, e um domínio apontando para ele. A porta do jogo fica interna à rede Docker; Caddy publica HTTPS/WebSocket nas portas 80 e 443 **do servidor**, sem qualquer mudança no roteador dos jogadores.

1. Copie esta pasta para a VPS.
2. Edite `Caddyfile`: substitua `salas.seu-dominio.com` pelo domínio real.
3. Na pasta do servidor, execute:

```sh
docker compose up -d --build
docker compose logs --tail=50 relay caddy
```

4. Confirme `https://SEU-DOMINIO/health`: deve responder `OK`.
5. Em `online.cfg`, coloque `url="wss://SEU-DOMINIO"` e distribua o cliente.

Caddy cuida do certificado e encaminha WebSockets ao relay. Documentação: [reverse_proxy](https://caddyserver.com/docs/caddyfile/directives/reverse_proxy) e [executar em Docker](https://caddyserver.com/docs/running).

## Funcionamento e limites

- Python 3.11 ou superior, sem pacotes externos. Também pode executar `python relay.py` atrás do seu proxy TLS.
- Até 8 pessoas por sala. A sala fica fechada para novos participantes depois de iniciar, mas aceita reconexões dos participantes originais.
- Um token secreto, mantido em memória pelo cliente, reserva a identidade por **90 segundos** após uma queda de conexão. Não compartilhe esse token; somente o código da sala.
- O anfitrião executa a simulação. O servidor registra salas, autentica a reconexão, identifica remetentes e encaminha dados. Os outros jogadores não abrem portas nem recebem conexões diretas.
- Sem migração de anfitrião: se ele sair voluntariamente, a sala encerra; se perder a conexão, a equipe espera sua reconexão. Sem retorno em 90 segundos, a sala encerra.
- Reconexão cobre queda temporária de rede com o jogo aberto. Fechar o cliente ou reiniciar o servidor perde a sessão em memória. Não há salvamento permanente de partidas cooperativas nesta versão.
- Salas ficam em memória em **uma única instância**. Não configure múltiplas réplicas sem acrescentar um armazenamento/roteador compartilhado.
- A simulação confia no anfitrião. Este é um cooperativo entre amigos, sem antitrapaça competitivo.
- Voz: PCM mono de 16 kHz, blocos de 40 ms, reprodução espacial com uma pequena reserva de áudio. Só é encaminhada a participantes a até 28 metros, no mesmo estado de vida, conforme a posição validada pelo anfitrião. Paredes abafam o áudio no cliente. Não há gravação de voz no servidor.
- No pior caso, com oito microfones transmitindo continuamente e todos próximos, planeje aproximadamente **1,8 MB/s de saída por sala**, mais o estado do jogo. Dimensione a hospedagem com medições reais; não há promessa de capacidade para centenas de salas.
- `MAX_ROOMS` controla o limite de salas (200 por padrão no programa; reduza conforme os recursos). `PORT` define a porta interna (8747 por padrão). `BIND` define a interface (0.0.0.0 por padrão).
- Limites de tamanho, frequência de mensagens, conexões e filas impedem crescimento ilimitado. Uma proteção de borda/proxy pode impor limites adicionais em uma publicação aberta ao público.

## Testar antes de distribuir

1. Hospede o servidor e configure `online.cfg` nos dois clientes.
2. Um computador cria a sala; outro, em uma conexão diferente, entra pelo código.
3. Ambos marcam pronto. Teste caminhada, porta, gerador e fala segurando V. Afaste-se para conferir a redução de volume.
4. Desconecte a rede de um cliente por alguns segundos e reconecte antes de 90 segundos. A identidade e os objetivos devem continuar.
5. No Windows, permita o acesso de aplicativos da área de trabalho ao microfone e escolha o dispositivo nas configurações. Use fones para evitar realimentação acústica.

Testes automatizados locais: inicie `python relay.py`, depois `python test_relay.py`. Esses testes validam protocolo e reconexão; **não substituem a verificação em duas redes reais e com microfones físicos** após publicar.
