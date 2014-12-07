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

    def _ping_check_thread(self):
        while self.running:
            time.sleep(1.0)
            for conn in self._clients.keys():
                #TODO: change the testing value to 250
                if self._clients[conn].last_pinged >= 250:
                    print "[DBG] %s disconnected because ping has timed-out" % \
                          str(conn.address)
                    self.disconnect(conn, "Ping timeout: 250 seconds")
                else:
                    self._clients[conn].last_pinged += 1

    def _ping_thread(self):
        while self.running:
            time.sleep(100.0)
            for client_conn in self._clients.keys():
                try:
                    client_conn.send("PING")
                except IOError:
                    for clnt in self._clients.keys():
                        if clnt == client_conn:
                            print "[DBG] %s disconnected because the socket is dead" % \
                                  str(clnt.address)
                            for cln in self._clients.values():
                                self.disconnect(clnt, "Remote host closed the connection")

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
            if command == "NICK":
                if len(command_args) < 1:
                    self._send_not_enough_parameters(conn, command)
                elif not conn in self._clients:
                    self._clients[conn] = Client(connection=conn, nick=command_args[0])
                    print "[DBG] Client connected!"
                else:
                    self._clients[conn].nick = command_args[0]
                    self._clients[conn].identifier = self._clients[conn].nick + "!" + \
                                                        command_args[0] + "@" + self.name
            elif command == "USER":
                self._send_lusers(conn)
                if conn in self._clients:
                    if len(command_args) < 1:
                        self._send_not_enough_parameters(conn, command)
                    else:
                        self._clients[conn].real_name = command_args[:]
                        self._clients[conn].identifier = self._clients[conn].nick + "!" + \
                                                               command_args[0] + "@" + self.name
                        self._send_motd(conn)
                else:  # Another way to identifyy is USER command.
                    if len(command_args) < 1:
                        self._send_not_enough_parameters(conn, command)
                    else:
                        self._clients[conn] = Client(connection=conn, nick=command_args[0])
                        self._send_motd(conn)
            elif command == "PRIVMSG" or command == "NOTICE":
                if len(command_args) < 2:
                    self._send_not_enough_parameters(conn, command)
                else:
                    src = self._clients[conn].identifier
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
                                    if clnt.nick != self._clients[conn].nick:
                                        if chan in clnt.channels:
                                            clnt.connection.send(":%s %s %s %s" %
                                                             (src, command, dest, "".join(command_args[1:])))
                                            break
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
                            self._clients[conn].channels.append(chan)

                            for client in self._clients.values():
                                if chan in client.channels:
                                    client.connection.send(
                                        ":%s JOIN %s" % (self._clients[conn].identifier, chan.name))
                            conn.send(":%s JOIN %s" % (self._clients[conn].identifier, chan.name))
                            self._send_topic(conn, chan)
                            self._send_names(conn, chan)
                    else:
                        chan = Channel(command_args[0], 1)
                        chan.users = 1  # We have a user, because we have created it!
                        self._channels.append(chan)
                        self._clients[conn].channels.append(chan)
                        conn.send(":%s JOIN %s" % (self._clients[conn].identifier,
                                                   command_args[0]))
            elif command == "PART":
                if len(command_args) < 1:
                    self._send_not_enough_parameters(conn, command)
                else:
                    for chan in self._channels:
                        if chan.name == command_args[0]:

                            for client in self._clients.values():
                                if chan in client.channels:
                                    print "[DBG] Notified %s about %s leaving the channel." % \
                                          (client.nick, self._clients[conn].nick)
                                    conn.send(":%s PART %s" % (self._clients[conn].identifier,
                                              command_args[0]))
                            self._clients[conn].channels.remove(chan)
                            chan.users -= 1
                            break
                    else:
                        self._send_no_channel(conn, command_args[0])


            elif command == "PING":
                if len(command_args):
                    conn.send("PONG %s" % command_args[1])
                else:
                    conn.send("PONG")
            elif command == "QUIT":
                for client in self._clients.values():
                    for channel in self._clients[conn].channels:
                        if channel in client.channels:
                            client.connection.send("%s QUIT %s" % (self._clients[conn].identifier,
                                                                   " ".join(command_args)))
                            break
                del self._clients[conn]  # It's dead
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
                            for client in self._clients.values():
                                if chan in client.channels:
                                    client.connection.send(":%s TOPIC %s %s" % (self._clients[conn].identifier,
                                                           chan.name, topic
                                                                                ))
                            break
                    else:
                        self._send_no_channel(conn, command_args[0])
            elif command == "QUIT":
                for client in self._clients:
                    for chan in self._channels:
                        if chan in client.channels:
                            client.connection.send(":%s QUIT %s" % (self._clients[conn].nick, " ".join(command_args)))
                            break
            elif command == "LUSERS":
                self._send_lusers(conn)

            elif command == "MOTD":
                self._send_motd(conn)

            elif command == "NAMES":
                if len(command_args) < 1:
                    self._send_not_enough_parameters(conn, command)
                else:
                    self._send_names(conn, command_args[0])
            elif command == "PONG":
                self._clients[conn].last_pinged = 0

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
        Thread(target=self._ping_check_thread).start()

    def stop(self):
        self.running = False

    def _send_motd(self, conn):
        nick = self._clients[conn].nick
        conn.send(":%s 375 %s :- Message of the day - " % (self.name, nick))
        for line in self.motd.split("\n"):
            conn.send(":%s 372 %s :- %s" % (self.name, nick, line))
        conn.send(":%s 376 %s :End of MOTD command" % (self.name, nick))

    def _send_names(self, conn, chan):
        names = []
        nick = self._clients[conn].nick
        for client in self._clients.values():
            if chan.name in client.channels:
                names.append(client.nick)
        conn.send(":%s 353 %s = %s :%s" % (self.name, nick, chan.name, " ".join(names)))
        conn.send(":%s 366 %s :End of NAMES list" % (self.name, chan.name))

    def _send_topic(self, conn, chan):
        nick = self._clients[conn].nick
        topic = chan.topic
        if len(topic):
            conn.send(":%s 332 %s %s" % (self.name, nick, chan.topic))
        else:
            conn.send(":%s 331 %s :No topic is set" % (self.name, nick))

    def _send_lusers(self, conn):
        """

       251    RPL_LUSERCLIENT
              ":There are <integer> users and <integer>
               services on <integer> servers"
       252    RPL_LUSEROP
              "<integer> :operator(s) online"
       253    RPL_LUSERUNKNOWN
              "<integer> :unknown connection(s)"
       254    RPL_LUSERCHANNELS
              "<integer> :channels formed"
       255    RPL_LUSERME
              ":I have <integer> clients and <integer>
                servers"
        """
        nick = self._clients[conn].nick
        conn.send(":%s 251 %s :There are %d users and 0 services on 1 servers" % (self.name, nick, len(self._clients)))
        conn.send(":%s 252 %s 0 :operator(s) online" % (self.name, nick))
        conn.send(":%s 253 %s 0 :unknown connection(s)" % (self.name, nick))
        conn.send(":%s 254 %s %d :channels formed" % (self.name, nick, len(self._channels)))
        conn.send(":%s 255 %s :I have %d clients and 1 servers" % (self.name, nick, len(self._clients)))


    def _send_no_channel(self, conn, chan_name):
        nick = self._clients[conn].nick
        conn.send(":%s 403 %s %s :No such channel" % (self.name, nick, chan_name))

    def _send_no_user(self, conn, target):
        nick = self._clients[conn].nick
        conn.send(":%s 401 %s %s :No such nick/channel" % (self.name, nick, target))

    def _send_not_enough_parameters(self, conn, command):
        nick = self._clients[conn].nick
        conn.send(":%s 461 %s %s :Not enough parameters" % (self.name, nick, command))

    def _send_unknown_command(self, conn, command):
        nick = self._clients[conn].nick
        conn.send(":%s 421 %s %s :Unknown command" % (self.name, nick, command))

    def disconnect(self, conn, message):
        client = self._clients[conn]
        for clnt in self._clients.values():
            for chan in clnt.channels:
                if chan in client.channels:
                    clnt.connection.send(":%s QUIT :%s" % client.identifier, message)
                    break
        try:
            conn.send("ERROR :Closing link [%s]: Disconnected" % conn.address)
        except IOError:
            pass
        del self._clients[conn]

    def __del__(self):
        self._sock.close()
        self.stop()
