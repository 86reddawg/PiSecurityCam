#!/usr/bin/python
####################################################################################################
#Security Cam Recorder - written by Joshua Hughes, 10/30/14
#
#After initialization, program waits in prefault buffer while loop waiting for trigger
#Prefault operates in 1 second blocks with FIFO functionality.
#When trigger picks up, prefault is written to file, then moves to live recording
#live recording appends to the file once per second until motion subsides
#once motion subsides, video continues to be appended in 1 second intervals for postfault time.
#For example, a 5s prefault, 3s motion, and 10s postfault would result in 18 seconds of video minimum plus
#intermediate file recording time (due to stdout buffer), plus any "rounding up" if motion
#stops in the middle of a 1 second recording block
#after event concludes, system returns to prefault buffer.
#####################################################################################################

#TODO - add ethernet trigger
#TODO - add parallel audio recording?
#TODO - lots of error handling - if os.killpg doesn't run at end, raspivid remains running. (kill on startup?)


import subprocess, time, os, signal, sys, fcntl, datetime, logging, socket, thread
import RPi.GPIO as GPIO

bitrate = 16000000	#video bitrate (bits/s)
width = 1920
height = 1080
prefault = 10	#will record up to prefault seconds, depending on how full the buffer is
postfault = 5	#will record at least postfault seconds, up to 1 sec more depending on when the motion subsides

#Server to inform of recordings (remote server needs to be running camserver.py)
server = '192.168.1.1'
port = 50007
deleteonack = True #This isn't implemented yet, server assumes all should be deleted

#The streamvideo option disables messages and instead dumps the raspivid stream back onto stdout. 
#Pipe this python program to streamer of choice or display will be flooded with text rendition of 
#h264 bitstream. For instance, to use cvlc to stream to http://localip:8090/, use something like:
#> pisecuritycam.py | cvlc - --sout '#standard{access=http,mux=ts{use-key-frames},dst=:8090}' :demux=h264
#see streamvlc script for example
streamvideo = True

startcode = b'\0\0\0\1'
cmd = 'raspivid -g 10 -n -t 0 -b ' + str(bitrate) + ' -rot 180 -w '+ str(width) +' -h '+ str(height) +' -fps 29 -pf high -sh 65 -br 60 -ex night -o -'
PIR_PIN = 21

#sys.stdout = os.fdopen(sys.stdout.fileno(), 'w', 0) # disable STDOUT buffer

def main():
    global p
    #start video stream to stdout
    p = subprocess.Popen(cmd, shell=True, stdout = subprocess.PIPE, preexec_fn = os.setsid)
    header, temp = getheader()
    fcntl.fcntl(p.stdout.fileno(), fcntl.F_SETFL, os.O_NONBLOCK) # this causes the read/readline to no longer wait to fill from stdout
    while True:
	try:
	    video,temp = buffer(temp) #wait for trigger, return buffer data plus extra data sent to recorder so no frame loss at trigger point
	    filename = str(time.time())+'.h264' #filename set to timestamp of trigger (move this to buffer for better resolution?)
	    write(filename,header)	#initialize file with header
	    append(filename,video)
	    ts = time.time()
	    temp = record(filename,temp)	#continue recording while motion active + postfault
	    display('Motion + post record time: '+str(time.time()-ts))
	    thread.start_new_thread(sendserver, (filename,))
	    time.sleep(1)
	except KeyboardInterrupt:
	    print 'Keyboard Interrupt'
	    break
    cleanup()

def record(filename,temp):
    display('Motion Recording...')
    post = time.time()
    while (motion or (time.time()-post < postfault)):
        if motion:	#while motion exists, keep postfault count fresh (this handles continual motion and retriggers)
	    post = time.time()
	else:
	    display('Post Recording Time Left: '+str(postfault - (time.time()-post)))
        start = temp[temp.find(startcode):] # get rid of all data before start string
        data = ''
	ts = time.time()
	while (time.time()-ts<1): #read one second worth of data
	    data += dataread('all') #keep reading until we hit one second
	temp = dataread('line')
	while startcode not in temp:
	    temp += dataread('line')
	finish = temp[:temp.find(startcode)] # get rid of all data  after next start string (including start string)
	temp = temp[temp.find(startcode):]
	data = start + data + finish # Each data[] has a complete start/end, no partial frames
	thread.start_new_thread(append, (filename,data))
	#append(filename,data)
    return temp

#Instead of building a header from scratch (decoding the spec is too much work), just scrape the header from
#when we first start up and apply that to all subsequent videos created. (header changes based on bitrate/fps/etc)
def getheader():
    data=''
    c = -1
    try:
	while c < 1:
	    data += dataread('line')
	    a = data.find(startcode)+len(startcode) #Finds initial startcode for header
	    b = data[a:].find(startcode)+len(startcode)+a #finds secondary startcode
	    c = data[b:].find(startcode)+b #third startcode should indicate the start of actual video frames
    except IOError:
        pass
	#TODO - better error handling... exit program if no header can be found as all vids will be corrupt
    data = data[:c] #This is the header
    temp = data[c:] #Pass the remaining bitstream back into play
    return data, temp


def buffer(temp):
    global motion
    display('Starting buffer...')
    dataread('all')	#clear out the buffer
    data = ['']*prefault # Data will be recorded in 1 second intervals and shifted in FIFO style
    while startcode not in temp:
        temp = dataread('line')
    while not motion:
	try:
	    #For efficiency, better idea to write data round robin, then figure out how to stitch together
	    #later instead of moving big chuncks of data every second?
    	    for y in range(0, prefault-1):	#shift data in FIFO style
		data[y] = data[y+1]		#0=1, 1=2, 2=3, 3=4, etc
    	    start = temp[temp.find(startcode):] # get rid of all data before start string
    	    data[prefault-1] = ''
	    ts = time.time()
	    while (time.time()-ts<1): #read one second worth of data
		data[prefault-1] += dataread('all') #keep reading until we hit 1 second
	    temp = dataread('line')
	    while startcode not in temp:
		temp += dataread('line')
	    finish = temp[:temp.find(startcode)] # get rid of all data  after next start string (including start string)
	    data[prefault-1] = start + data[prefault-1] + finish # Each data[] has a complete start/end, no partial frames
	except KeyboardInterrupt:
	    display('Keyboard Interrupt')
	    cleanup()
    return ''.join(data),temp	#concatenate all data together, pass remaining data back into play
#    return data[0]+data[1]+data[2]+data[3]+data[4]

def append(filename,video):
    filename = '/home/picam/video/'+filename
    with open(filename, 'a') as file_:
        file_.write(video)	#append to video file to prevent using up memory for long recordings

def write(filename,video):
    filename = '/home/picam/video/'+filename
    with open(filename, 'w') as file_:
	os.chmod(filename, 0777)
        file_.write(video)


def dataread(amt):
    while True:
        grab = ''
        try:
	    if amt == 'line':
        	grab += bytes(p.stdout.readline())
	    if amt == 'all':
        	grab += bytes(p.stdout.read())
	    else:
        	grab += bytes(p.stdout.readline())
        except IOError:
	    pass
	if grab != '':
	    break
    if streamvideo:
	sys.stdout.write(grab)
    return grab

def display(string):
    ts = datetime.datetime.fromtimestamp(time.time()).strftime('%Y-%m-%d %H:%M:%S')
    output = ts+' - '+string
    if not streamvideo:
	print output
    return output

def cleanup():
    os.killpg(p.pid, signal.SIGTERM) # Stop continuous raspivid
    sys.exit()

def MOTION(PIR_PIN):
    global motion
    if GPIO.input(PIR_PIN):
	logger.warning(display('Motion Detected'))
	motion = True
    else:
	logger.warning(display('Motion Stopped'))
	motion = False

def InitLogger():
    global logger
    logger = logging.getLogger('Motion')
    hdlr = logging.FileHandler('/var/log/motion.log')
    hdlr.setFormatter(logging.Formatter('%(message)s'))
    logger.addHandler(hdlr)
    logger.setLevel(logging.WARNING)

def getserial():
    cpuserial = '0'
    try:
	f = open('/proc/cpuinfo', 'r')
	for line in f:
	    if line[0:6]=='Serial':
		cpuserial = line[10:26]
	f.close()
    except:
	cpuserial = "Error"
    return cpuserial

def sendserver(string):
    #TODO - much needed error handling here
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.connect((server, port))
    s.send(cpuserial+','+string)
    recv = s.recv(4096)
    s.close()
    display(recv) # If file grabbed, server returns ACK <serial><filename>
#    return recv

def InitPIR():
    GPIO.setmode(GPIO.BCM)
    GPIO.setup(PIR_PIN, GPIO.IN, pull_up_down=GPIO.PUD_DOWN)
    logger.warning(display('Motion Sensor Startup'))
    time.sleep(1) # time delay to let PIR sensor settle
    GPIO.add_event_detect(PIR_PIN, GPIO.BOTH, callback=MOTION, bouncetime=100) #as opposed to GPIO.RISING

if __name__ == "__main__":
    global motion, cpuserial
    cpuserial = getserial()
    motion = False
    InitLogger()
    InitPIR()
    main()

