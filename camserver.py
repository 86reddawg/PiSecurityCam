#!/usr/bin/python
#Run this on the main server to collect recordings from the various pi security cams
#Note, this is a very rough draft

HOST = ''
PORT = 50007

import socket, ftplib, sys, os

def download(server,device,filename):
    print server,device,filename
    ftp = ftplib.FTP(server, 'picam', 'picam')
    ftp.cwd('video')
    location = '/mnt/raid0/Tempz/'+filename
    ftp.retrbinary('RETR %s' % filename, open(location,'wb').write)
    os.chmod(location, 0777)
    ftp.delete(filename)
    ftp.quit()
    print filename, 'downloaded from '+device

def cleanup():
    s.shutdown(socket.SHUT_RDWR)
    s.close()
    sys.exit()

s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
s.bind((HOST, PORT))
s.listen(5)

while True:
    try:
	conn,addr = s.accept()
	buf = conn.recv(4096)
	if len(buf) > 0:
	    print addr[0],'-', buf
	    if len(buf) > 10:
	        device = buf[:buf.find(',')]
	        filename = buf[buf.find(',')+1:]
	        download(addr[0],device,filename)
	    conn.send('ACK '+buf)
	if len(buf) == 0:
	    print '0'
    except KeyboardInterrupt:
	print 'Keyboard Interrupt'
	cleanup()
