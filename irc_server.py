import time

from channel import Channel
from client import Client


__author__ = 'mark'
from Queue import Queue
import socket
import connection
from threading import Thread


class IRCServer(object):
    _message_queue = Queue()
    _sock = None
    _connections = None
    _bind_address = None
    running = None
    _clients = None
    _channels = None

    def _ping_thread(self):
        while self.running:
            time.sleep(250.0)
            for client_hash in self._clients.keys():
                try:
                    self._clients[client_hash].connection.send("PING")
                except IOError:
                    for clnt in self._clients.keys():
                        if clnt == client_hash:
                            print "[DBG] %s disconnected because the socket is dead" % \
                                  str(self._clients[clnt].connection.address)
                            for cln in self._clients.values():
                                cln.connection.send("%s QUIT :Connection closed" % self._clients[clnt].nick)
                            del self._clients[clnt]

    def _message_thread(self):
        while self.running:
            for conn in self._connections:
                try:
                    for msg in conn.get_messages():
                        self._message_queue.put(msg)
                except IOError:  # We couldn't read from socket, thus the connection is dead.
                    print "[DBG] can't read from connection %s" % str(conn)
                    self._connections.remove(conn)

    def _message_handler_thread(self):
        while self.running:
            msg = self._message_queue.get(True)
            text = msg.get_data()
            conn = msg.get_connection()
            args = text.replace("\r", "").replace("\n", "").split(" ")
            command = args[0].upper()
            command_args = args[1:]
            print command_args
            if command == "NICK":
                if len(command_args) < 1:
                    self._send_not_enough_parameters(conn, command)
                elif not hash(conn) in self._clients:
                    self._clients[hash(conn)] = Client(connection=conn, nick=command_args[0])
                    print "[DBG] Client connected!"
                else:
                    self._clients[hash(conn)].nick = command_args[0]
            elif command == "USER":
                if hash(conn) in self._clients:
                    if len(command_args) < 1:
                        self._send_not_enough_parameters(conn, command)
                    else:
                        self._clients[hash(conn)].real_name = command_args[:]
                        self._clients[hash(conn)].identifier = self._clients[hash(conn)].nick + "!" + \
                                                               command_args[0] + "@" + self.name
                        self._send_motd(conn)
                else:  # Another way to identifyy is USER command.
                    if len(command_args) < 1:
                        self._send_not_enough_parameters(conn, command)
                    else:
                        self._clients[hash(conn)] = Client(connection=conn, nick=command_args[0])
                        self._send_motd(conn)
            elif command == "PRIVMSG" or command == "NOTICE":
                if len(command_args) < 2:
                    self._send_not_enough_parameters(conn, command)
                else:
                    src = self._clients[hash(conn)].identifier
                    dest = command_args[0]
                    if not dest.startswith("#"):
                        for clnt in self._clients.values():
                            if clnt.nick == dest:
                                clnt.connection.send(
                                    ":%s %s %s %s" % (src, command, dest, "".join(command_args[1:]))
                                )
                                break
                        else:
                            self._send_no_user(conn, dest)
                    else:
                        for chan in self._channels:
                            if chan.name == dest:
                                for clnt in self._clients.values():
                                    if chan in clnt.channels:
                                        clnt.connection.send(":%s %s %s %s" %
                                                             (src, command, dest, "".join(command_args[1:])))
                                        break
                        else:
                            self._send_no_user(conn, dest)
            elif command == "JOIN":
                if len(command_args) < 1:
                    self._send_not_enough_parameters(conn, command)
                else:
                    for chan in self._channels:
                        if chan.name == command_args[0]:
                            chan.users += 1
                            self._clients[hash(conn)].channels.append(chan)
                            # conn.send(":%s JOIN %s" % (self._clients[hash(conn)].identifier, chan.name))
                            self._clients[hash(conn)].channels.remove(chan)
                            chan.users -= 1
                            for client in self._clients.values():
                                if chan in client.channels:
                                    client.connection.send(
                                        ":%s JOIN %s" % (self._clients[hash(conn)].identifier, chan.name))
                            conn.send(":%s JOIN %s" % (self._clients[hash(conn)].identifier, chan.name))
                            self._send_topic(conn, chan)
                            self._send_names(conn, chan)
                    else:
                        chan = Channel(command_args[0], 1)
                        chan.users = 1  # We have a user, because we have created it!
                        self._channels.append(chan)
                        self._clients[hash(conn)].channels.append(chan)
                        conn.send(":%s JOIN %s" % (self._clients[hash(conn)].identifier,
                                                   command_args[0]))
            elif command == "PART":
                if len(command_args) < 1:
                    self._send_not_enough_parameters(conn, command)
                else:
                    for chan in self._channels:
                        if chan.name == command_args[0]:
                            self._clients[hash(conn)].channels.remove(chan)
                            chan.users -= 1
                            for client in self._clients.values():
                                if chan in client.channels:
                                    client.connection.send(":%s PART %s" % (self._clients[hash(conn)], chan.name))
            elif command == "PING":
                if len(command_args):
                    conn.send("PONG %s" % command_args[1])
                else:
                    conn.send("PONG")
            elif command == "QUIT":
                for client in self._clients.values():
                    for channel in self._clients[hash(conn)].channels:
                        if channel in client.channels:
                            client.connection.send("%s QUIT %s" % (self._clients[hash(conn)].nick,
                                                                   " ".join(command_args)))
                            break
                    else:
                        self._send_no_channel(conn, command_args[0])
            elif command == "TOPIC":
                if len(command_args) < 1:
                    self._send_not_enough_parameters(conn, command)
                elif len(command_args) == 1:
                    for chan in self._channels:
                        if chan.name == command_args[0]:
                            self._send_topic(conn, chan)
                            break
                elif len(command_args) == 2:
                    for chan in self._channels:
                        if chan.name == command_args[0]:
                            topic = command_args[1]
                            if topic[0] == ":":
                                topic = topic[1:]
                            chan.topic = topic
                            break
                    else:
                        self._send_no_channel(conn, command_args[0])

    def _server_thread(self):
        self._connections = []
        self._clients = {}
        self._channels = []
        while True:
            try:
                self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                self._sock.bind(self._bind_address)
                break
            except socket.error:
                pass
        self._sock.listen(1)
        print "Listening..."
        while self.running:
            sock, address = self._sock.accept()
            sock.setblocking(0)
            conn = connection.Connection(address, sock)
            self._connections.append(conn)
            conn.send(":%s 251 %s :There are %d users on 1 server." % (self.name, len(self._clients), self.name))
            print "Connection! %s" % str(address)

    def __init__(self, bind_address, name="server", motd="Hello, World"):
        self._message_queue = Queue()
        self._bind_address = bind_address
        self.running = False
        self.name = name
        self.motd = motd

    def start(self):
        self.running = True
        Thread(target=self._server_thread).start()
        Thread(target=self._message_handler_thread).start()
        Thread(target=self._message_thread).start()
        Thread(target=self._ping_thread).start()

    def stop(self):
        self.running = False

    def _send_motd(self, conn):
        nick = self._clients[hash(conn)].nick
        conn.send(":%s 375 %s :- Message of the day - " % (self.name, nick))
        for line in self.motd.split("\n"):
            conn.send(":%s 372 %s :- %s" % (self.name, nick, line))
        conn.send(":%s 376 %s :End of MOTD command" % (self.name, nick))

    def _send_names(self, conn, chan):
        names = []
        nick = self._clients[hash(conn)].nick
        for client in self._clients.values():
            if chan.name in client.channels:
                names.append(client.nick)
        conn.send(":%s 353 %s = %s :%s" % (self.name, nick, chan.name, " ".join(names)))
        conn.send(":%s 366 %s :End of NAMES list" % (self.name, chan.name))

    def _send_topic(self, conn, chan):
        nick = self._clients[hash(conn)].nick
        topic = chan.topic
        if len(topic):
            conn.send(":%s 332 %s %s" % (self.name, nick, chan.topic))
        else:
            conn.send(":%s 331 %s :No topic is set" % (self.name, nick))

    def _send_no_channel(self, conn, chan_name):
        nick = self._clients[hash(conn)].nick
        conn.send(":%s 403 %s %s :No such channel" % (self.name, nick, chan_name))

    def _send_no_user(self, conn, target):
        nick = self._clients[hash(conn)].nick
        conn.send(":%s 401 %s %s :No such nick/channel" % (self.name, nick, target))

    def _send_not_enough_parameters(self, conn, command):
        nick = self._clients[hash(conn)].nick
        conn.send(":%s 461 %s %s :Not enough parameters" % (self.name, nick, command))

    def __del__(self):
        self._sock.close()
        self.stop()
