__author__ = 'mark'


class Client(object):
    def __init__(self, connection, nick):
        self.nick = nick
        self.connection = connection
        self.real_name = None
        self.identifier = None
        self.channels = []