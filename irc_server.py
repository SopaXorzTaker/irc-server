import time
import string
from queue import Queue
from channel import Channel
from client import Client


__author__ = 'mark'
import socket
import connection
from threading import Thread

SPECIAL = "[]\`_^{|}"
ALLOWED_NICKNAME = string.digits + string.ascii_letters + SPECIAL + "-"
# noinspection PyUnboundLocalVariable
ALLOWED_CHANNEL = "".join([chr(x) for x in range(128)]).replace("\0", "").replace("\7", "").replace("\13", "").replace("\10", "")\
    .replace(" ", "").replace(",", "").replace(":", "")


class IRCServer(object):
    _message_queue = Queue()
    _sock = None
    _connections = None
    _bind_address = None
    running = None
    _clients = None
    _channels = None
    _nick_change_failed = []

    '''
        This prevents memory leak when the client who had error was disconnected
    '''
    def _dead_check_thread(self):
        while self.running:
            for conn in self._nick_change_failed:
                for client_conn in self._clients:
                    if client_conn == conn:
                        break
                else:
                    self._nick_change_failed.remove(conn)

    def _ping_check_thread(self):
        while self.running:
            time.sleep(1.0)
            for conn in self._clients.keys():
                if self._clients[conn].last_pinged >= 250:
                    print("[DBG] %s disconnected because ping has timed-out" %
                          str(conn.address))
                    self.disconnect(conn, "Ping timeout: 250 seconds")
                else:
                    self._clients[conn].last_pinged += 1

    def _ping_thread(self):
        while self.running:
            time.sleep(100.0)
            for client_conn in self._clients.keys():
                try:
                    self._clients[client_conn].send("PING")
                except IOError:
                    self.disconnect(client_conn, "Remote host closed the connection")

    def _message_thread(self):
        while self.running:
            for conn in self._connections:
                try:
                    for msg in conn.get_messages():
                        self._message_queue.put(msg)
                except IOError:  # We couldn't read from socket, thus the connection is dead.
                    print("[DBG] can't read from connection %s" % str(conn))
                    self._connections.remove(conn)

    def _message_handler_thread(self):
        # TODO: this may leak memory when clients die, fix it later
        self._nick_change_failed = []
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
                else:
                    if not self._set_nick(conn, command_args[0]):
                        self._nick_change_failed.append(conn)
            elif command == "USER":
                if conn in self._clients:
                    if len(command_args) < 1:
                        self._send_not_enough_parameters(conn, command)
                    else:
                        self._send_lusers(conn)
                        self._clients[conn].real_name = command_args[:]
                        self._clients[conn].identifier = self._clients[conn].get_nick() + "!" + \
                                                               command_args[0] + "@" + self.name
                        self._send_motd(conn)
                else:  # Another way to identify is USER command.
                    if len(command_args) < 1:
                        self._send_not_enough_parameters(conn, command)
                    elif conn in self._nick_change_failed:
                        self._nick_change_failed.remove(conn)
                    else:
                        if self._set_nick(conn, command_args[0]):
                            self._send_motd(conn)
            elif command == "PRIVMSG" or command == "NOTICE":
                if len(command_args) < 2:
                    self._send_not_enough_parameters(conn, command)
                else:
                    message_text = command_args[1] if not command_args[1][0] == ":" else \
                        text.replace("\r\n", "")[text.index(":"):]
                    src = self._clients[conn].get_identifier()
                    dest = command_args[0]
                    if not dest.startswith("#"):
                        for clnt in self._clients.values():
                            if clnt.nick == dest:
                                clnt.connection.send(
                                    ":%s %s %s :%s" % (src, command, dest, message_text)
                                )
                                break
                        else:
                            self._send_no_user(conn, dest)
                    else:
                        for chan in self._channels:
                            if chan.name == dest:
                                self._channel_broadcast(conn, chan, ":%s %s %s :%s" %
                                            (src, command, dest, message_text))
                                break
                        else:
                            self._send_no_user(conn, dest)
            elif command == "JOIN":
                if len(command_args) < 1:
                    self._send_not_enough_parameters(conn, command)
                elif not all(c in ALLOWED_CHANNEL for c in command_args[0]) and len(command_args[0]):
                    self._send_no_channel(conn, command_args[0])
                else:
                    for chan in self._channels:
                        if chan.name == command_args[0]:
                            chan.users += 1
                            self._clients[conn].channels.append(chan)
                            self._send_to_related(conn, ":%s JOIN %s" % (self._clients[conn].get_identifier(),
                                                                         chan.name), True)
                            self._send_topic(conn, chan)
                            self._send_names(conn, chan)
                    else:
                        chan = Channel(command_args[0], 1)
                        chan.users = 1  # We have a user, because we have created it!
                        self._channels.append(chan)
                        self._clients[conn].channels.append(chan)
                        self._clients[conn].send(":%s JOIN %s" % (self._clients[conn].get_identifier(),
                                                   command_args[0]))
            elif command == "PART":
                if len(command_args) < 1:
                    self._send_not_enough_parameters(conn, command)
                else:
                    for chan in self._channels:
                        if chan.name == command_args[0]:

                            self._send_to_related(conn, ":%s PART %s" % (self._clients[conn].get_identifier(),
                                                                         command_args[0]))
                            self._clients[conn].channels.remove(chan)
                            chan.users -= 1
                            break
                    else:
                        self._send_no_channel(conn, command_args[0])

            elif command == "PING":
                if len(command_args):
                    self._clients[conn].send("PONG %s" % command_args[1])
                else:
                    self._clients[conn].send("PONG")
            elif command == "QUIT":
                for client in self._clients.values():
                    for channel in self._clients[conn].channels:
                        if channel in client.channels:
                            client.connection.send("%s QUIT %s" % (self._clients[conn].get_identifier(),
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
                                    client.connection.send(":%s TOPIC %s %s" % (self._clients[conn].get_identifier(),
                                                           chan.name, topic
                                                                                ))
                            break
                    else:
                        self._send_no_channel(conn, command_args[0])
            elif command == "QUIT":
                for client in self._clients:
                    for chan in self._channels:
                        if chan in client.channels:
                            client.connection.send(":%s QUIT %s" % (self._clients[conn].get_nick(), " ".join(command_args)))
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
        attempt = 0
        while True:
            try:
                self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                self._sock.bind(self._bind_address)
                break
            except socket.error:
                if attempt == 5:
                    print("[FATAL] unable to open socket after 5 attempts, stopping.")
                    self.stop()
                    return

                print("[DEBUG] unable to open socket, retry in 10 seconds")
                attempt += 1
                time.sleep(10)
        self._sock.listen(1)
        print("Listening...")
        while self.running:
            sock, address = self._sock.accept()
            sock.setblocking(0)
            conn = connection.Connection(address, sock)
            self._connections.append(conn)
            print("Connection! %s" % str(address))

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
        Thread(target=self._dead_check_thread).start()
        Thread(target=self._ping_thread).start()
        Thread(target=self._ping_check_thread).start()

    def stop(self):
        self.running = False

    def _send_motd(self, conn):
        nick = self._clients[conn].get_nick()
        self._clients[conn].send(":%s 375 %s :- Message of the day - " % (self.name, nick))
        for line in self.motd.split("\n"):
            self._clients[conn].send(":%s 372 %s :- %s" % (self.name, nick, line))
        self._clients[conn].send(":%s 376 %s :End of MOTD command" % (self.name, nick))

    def _send_names(self, conn, chan):
        names = []
        nick = self._clients[conn].get_nick()
        for client in self._clients.values():
            if chan.name in client.channels:
                names.append(client.nick)
        self._clients[conn].send(":%s 353 %s = %s :%s" % (self.name, nick, chan.name, " ".join(names)))
        self._clients[conn].send(":%s 366 %s :End of NAMES list" % (self.name, chan.name))

    def _send_topic(self, conn, chan):
        nick = self._clients[conn].get_nick()
        topic = chan.topic
        if len(topic):
            self._clients[conn].send(":%s 332 %s %s" % (self.name, nick, chan.topic))
        else:
            self._clients[conn].send(":%s 331 %s :No topic is set" % (self.name, nick))

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
        nick = self._clients[conn].get_nick()
        self._clients[conn].send(":%s 251 %s :There are %d users and 0 services on 1 servers" % (self.name, nick, len(self._clients)))
        self._clients[conn].send(":%s 252 %s 0 :operator(s) online" % (self.name, nick))
        self._clients[conn].send(":%s 253 %s 0 :unknown connection(s)" % (self.name, nick))
        self._clients[conn].send(":%s 254 %s %d :channels formed" % (self.name, nick, len(self._channels)))
        self._clients[conn].send(":%s 255 %s :I have %d clients and 1 servers" % (self.name, nick, len(self._clients)))

    def _send_no_channel(self, conn, chan_name):
        nick = self._clients[conn].get_nick()
        self._clients[conn].send(":%s 403 %s %s :No such channel" % (self.name, nick, chan_name))

    def _send_no_user(self, conn, target):
        nick = self._clients[conn].get_nick()
        self._clients[conn].send(":%s 401 %s %s :No such nick/channel" % (self.name, nick, target))

    def _send_not_enough_parameters(self, conn, command):
        nick = self._clients[conn].get_nick()
        self._clients[conn].send(":%s 461 %s %s :Not enough parameters" % (self.name, nick, command))

    def _send_unknown_command(self, conn, command):
        nick = self._clients[conn].get_nick()
        self._clients[conn].send(":%s 421 %s %s :Unknown command" % (self.name, nick, command))

    def _send_nickname_in_use(self, conn, nick):
        conn.send(":%s 433 %s :Nickname already in use" % (self.name, nick))

    def _send_erroneous_nickname(self, conn, nick):
        conn.send(":%s 432 %s :Erroneous nickname" % (self.name, nick))

    def disconnect(self, conn, message):
        client = self._clients[conn]
        self._send_to_related(conn, ":%s QUIT :%s" % client.identifier, message)
        try:
            self._clients[conn].send("ERROR :Closing link [%s]: Disconnected" % conn.address)
        except IOError:
            pass
        del self._clients[conn]

    def _send_to_related(self, conn, msg, ignore_self=False):
        clnt = self._clients[conn]
        for client in self._clients.values():
            if ignore_self and client != conn:
                continue

            for channel in client.channels:
                if channel in clnt.channels:
                    client.connection.send(msg)
                    break

    def _channel_broadcast(self, conn, chan, msg):
        for client in self._clients.values():
            if client.nick == self._clients[conn].nick:
                continue
            if chan in client.channels:
                client.send(msg)


    def _nick_in_use(self, nick):
        """
        :param nick: Nickname of client
        :type nick: str
        :return: True if a client with that name exists, else False
        """
        for client in self._clients.values():
            if client.nick == nick:
                return True
        else:
            return False


    def _valid_nick(self, nick):
        if not all(c in ALLOWED_NICKNAME for c in nick) or len(nick) > 9 or\
                not len(nick):
            return False
        return True

    def _set_nick(self, conn, nick):
        if self._nick_in_use(nick):
            self._send_nickname_in_use(conn, nick)
            return False
        elif not self._valid_nick(nick):
            self._send_erroneous_nickname(conn, nick)
            return False
        else:
            if not conn in self._clients:
                self._clients[conn] = Client(connection=conn, nick=nick)
            else:
                self._clients[conn].nick = nick
            return True


    def __del__(self):
        self._sock.close()
        del self._sock
        self.stop()
