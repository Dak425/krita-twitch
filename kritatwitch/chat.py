from kritatwitch.vendor import twitch

def handle_message(message: twitch.chat.Message) -> None:
    if message.text.startswith('?hello'):
        message.chat.send('Hello World')