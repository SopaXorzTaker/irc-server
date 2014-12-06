__author__ = 'mark'


class Message(object):
    def __init__(self, data, connection):
        self.data = data
        self.connection = connection

    def get_data(self):
        return self.data

    def get_connection(self):
        return self.connection

    def send(self):
        self.connection.send(self.data)
