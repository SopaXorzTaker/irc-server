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
                except:
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
                    conn.send("461 %s :Not enough parameters" % command)
                elif not hash(conn) in self._clients:
                    self._clients[hash(conn)] = Client(connection=conn, nick=command_args[0])
                    print "[DBG] Client connected!"
                else:
                    self._clients[hash(conn)].nick = command_args[0]
            elif command == "USER":
                if hash(conn) in self._clients:
                    self._clients[hash(conn)].real_name = command_args[:]
                    self._send_motd(conn)
                else:  # Another way to identifyy is USER command.
                    if len(command_args) < 1:
                        conn.send("461 %s :Not enough parameters" % command)
                    else:
                        self._clients[hash(conn)] = Client(connection=conn, nick=command_args[0])
                        self._send_motd(conn)
            elif command == "PRIVMSG" or command == "NOTICE":
                if len(command_args) < 2:
                    conn.send("461 %s :Not enough parameters" % command)
                else:
                    src = self._clients[hash(conn)].nick
                    dest = command_args[0]
                    if not dest.startswith("#"):
                            for clnt in self._clients.values():
                                if clnt.nick == dest:
                                    clnt.connection.send(
                                        ":%s %s %s %s" % (src, command, dest, "".join(command_args[1:]))
                                    )
                    else:
                        for chan in self._channels:
                            if chan.name == dest:
                                for clnt in self._clients.values():
                                    if chan in clnt.channels:
                                        clnt.connection.send(":%s %s %s %s" %
                                                             (src, command, dest, "".join(command_args[1:])))
            elif command == "JOIN":
                if len(command_args) < 1:
                    conn.send("461 %s :Not enough parameters" % command)
                else:
                    for chan in self._channels:
                        if chan.name == command_args[0]:
                            chan.users += 1
                            self._clients[hash(conn)].channels.append(chan)
                            # conn.send(":%s JOIN %s" % (self._clients[hash(conn)].nick, chan.name))
                            self._clients[hash(conn)].channels.remove(chan)
                            chan.users -= 1
                            for client in self._clients.values():
                                if chan in client.channels:
                                    client.connection.send(":%s JOIN %s" % (self._clients[hash(conn)].nick, chan.name))
                            conn.send(":%s JOIN %s" % (self._clients[hash(conn)].nick, chan.name))
                            self._send_names(conn, chan)
                    else:
                        chan = Channel(command_args[0], 1)
                        chan.users = 1  # We have a user, because we have created it!
                        self._channels.append(chan)
                        self._clients[hash(conn)].channels.append(chan)
                        conn.send(":%s JOIN %s" % (self._clients[hash(conn)].nick,
                                                   command_args[0]))
            elif command == "PART":
                if len(command_args) < 1:
                    conn.send("461 %s :Not enough parameters" % command)
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

    def _server_thread(self):
        self._connections = []
        self._clients = {}
        self._channels = []
        while True:
            try:
                self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                self._sock.bind(self._bind_address)
                break
            except socket.error as err:
                # print "Failed to open socket, attempting again: %s" % err.message
                pass
        self._sock.listen(1)
        print "Listening..."
        while self.running:
            sock, address = self._sock.accept()
            sock.setblocking(0)
            conn = connection.Connection(address, sock)
            self._connections.append(conn)
            conn.send(":boo 251 boo :There are 0 users on 1 server.")
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
        conn.send("375 :- %s Message of the day - " % self.name)
        for line in self.motd.split("\n"):
            conn.send("372 :- %s" % line)
        conn.send("376 :End of MOTD command")

    def _send_names(self, conn, chan):
        names = []
        for client in self._clients.values():
            if chan.name in client.channels:
                names.append(client.nick)
        conn.send("353 %s = %s :%s" % (self._clients[hash(conn)].nick, chan.name, " ".join(names)))
        conn.send("366 %s :End of NAMES list" % chan.name)

    def __del__(self):
        self._sock.close()
        self.stop()