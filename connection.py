import socket
from message import Message

__author__ = 'mark'


class Connection(object):
    address = None
    _connection = None

    def __init__(self, address, connection):
        self.address, self._connection = address, connection

    def get_messages(self):
        messages = []
        data = ""
        while True:
            try:
                buff = self._connection.recv(2048)
                data += buff
            except socket.error:
                break
        for line in data.split("\r\n"):
            if len(line):
                messages.append(Message(line, self))

        return messages

    def send(self, data):
        self._connection.send(data + "\r\n")



