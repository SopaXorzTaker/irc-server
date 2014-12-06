__author__ = 'mark'
import irc_server

serv = irc_server.IRCServer(("", 6668))
serv.start()