__author__ = 'mark'


class Channel(object):
    def __init__(self, name, users):
        self.name, self.users, self.topic = name, users, ""