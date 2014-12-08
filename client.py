__author__ = 'mark'


class Client(object):
    def __init__(self, connection, nick):
        self.nick = nick
        self.connection = connection
        self.real_name = None
        self.identifier = None
        self.channels = []
        self.last_pinged = 0

    def get_nick(self):
        return self.nick

    def get_identifier(self):
        return self.identifier

    def get_real_name(self):
        return self.real_name

    def send(self, data):
        self.connection.send(data)