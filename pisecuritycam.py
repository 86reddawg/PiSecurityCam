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

#TODO - Flesh out server - instead of grabbing files over FTP, use sockets?
#TODO - add parallel audio recording - Currently exists but way out of sync... due to 
#	way that video is harvested in one sec chunks I think.
#TODO - lots of error handling - if os.killpg doesn't run at end, raspivid remains running. (kill on startup?)


import subprocess, time, os, signal, sys, fcntl, datetime, logging, socket, thread
import pyaudio, collections, wave
import threading
from ctypes import *
import RPi.GPIO as GPIO

bitrate = 1000000	#video bitrate (bits/s)
width = 1280
height = 960
fps = 30
prefault = 5	#will record up to prefault seconds, depending on how full the buffer is
postfault = 7	#will record at least postfault seconds, up to 1 sec more depending on when the motion subsides

#Server to inform of recordings (remote server needs to be running camserver.py)
SendToServer = False
server = '192.168.1.1'
port = 50007
deleteonack = True #This isn't implemented yet, server assumes all should be deleted

#The streamvideo option disables messages and instead dumps the raspivid stream back onto stdout. 
#Pipe this python program to streamer of choice or display will be flooded with text rendition of 
#h264 bitstream. For instance, to use cvlc to stream to http://localip:8090/, use something like:
#> pisecuritycam.py | cvlc - --sout '#standard{access=http,mux=ts{use-key-frames},dst=:8090}' :demux=h264
#see streamvlc script for example
streamvideo = False

startcode = b'\0\0\0\1'
cmd = 'raspivid -g 15 -n -t 0 -b ' + str(bitrate) + ' -rot 180 -w '+ str(width) +' -h '+ str(height) +' -fps '+ str(fps) +' -pf high -sh 65 -br 70 -ex night -o -'
PIR_PIN = 21

#Audio setup:
CHUNK = 1000
FORMAT = pyaudio.paInt16
CHANNELS = 1
RATE = 48000
AINDEX=2 #Audio device index
#Note, if prefault*RATE/CHUNK is not an integer, rounding error causes decrease in time size

vext = '.h264'
aext = '.wav'
#sys.stdout = os.fdopen(sys.stdout.fileno(), 'w', 0) # disable STDOUT buffer


def callback(in_data, frame_count, time_info, status):
    global abuffer, arecord, buffering, recording
    if buffering:
        abuffer.append(in_data)
    if recording:
        arecord += in_data
    return (in_data, pyaudio.paContinue)

def main():
    global p, a, abuffer, astream, arecord
    global buffering, recording
    buffering = False
    recording = False
    #start video stream to stdout
    p = subprocess.Popen(cmd, shell=True, bufsize=bitrate, stdout = subprocess.PIPE, preexec_fn = os.setsid)
    fcntl.fcntl(p.stdout.fileno(), fcntl.F_SETFL, os.O_NONBLOCK) # this causes the read/readline to no longer wait to fill from stdout
    header, temp = getheader()
    ERROR_HANDLER_FUNC = CFUNCTYPE(None, c_char_p, c_int, c_char_p, c_int, c_char_p)
    #Handle the pile of audio driver messages that crap up the stdout stream
    #Possibly change video stream to FIFO file instead of STDOUT?
    def py_error_handler(filename, line, function, err, fmt):
	pass
    c_error_handler = ERROR_HANDLER_FUNC(py_error_handler)
    asound = cdll.LoadLibrary('libasound.so')
    # Set error handler
    asound.snd_lib_error_set_handler(c_error_handler)
    a = pyaudio.PyAudio()
    abuffer = collections.deque(maxlen=(RATE/CHUNK)*prefault) 
    arecord = ''

    astream = a.open(format=FORMAT,
		    channels=CHANNELS,
		    rate=RATE,
		    input=True,
		    frames_per_buffer=CHUNK,
		    input_device_index=AINDEX,
		    start=False,
		    stream_callback=callback)

    while True:
	try:
	    thread.start_new_thread(audiobuffer, ())
	    video,temp = buffer(temp) #wait for trigger, return buffer data plus extra data sent to recorder so no frame loss at trigger point
	    recording = True
	    buffering = False
	    filename = str(time.time()) #filename set to timestamp of trigger (move this to buffer for better resolution?)
	    write(filename,header,vext, 'w')	#initialize file with header
	    write(filename,video,vext, 'a')
	    write(filename,''.join(abuffer),aext, 'w')
	    ts = time.time()
	    temp = record(filename,temp)	#continue recording while motion active + postfault
	    recording = False
	    createwave(filename,aext) #add header with total number of frames
	    display('Motion + post record time: '+str(time.time()-ts))
	    if SendToServer: thread.start_new_thread(sendserver, (filename,))
	except KeyboardInterrupt:
	    print 'Keyboard Interrupt'
	    break
    cleanup()

def audiobuffer():
    global astream, buffering, recording
    buffering = True
    recording = False
    astream.start_stream()
    while buffering:
	pass
	time.sleep(.01)
    while recording:
	pass
	time.sleep(.01)
    astream.stop_stream()
#    recording = True
#    buffering = False

def record(filename,temp):
    global arecord
    display('Motion Recording...')
    post = time.time()
    vrecord = ''
    while (motion or (time.time()-post < postfault)):
        if motion: post = time.time()	#while motion exists, keep postfault count fresh (this handles continual motion and retriggers)
	else: display('Post Recording Time Left: '+str(postfault - (time.time()-post)))
	start = temp + dataread('all')
        #start = temp[temp.find(startcode):] # get rid of all data before start string
#	ts = time.time()
#	while (time.time()-ts<1): #read one second worth of data
#	    data += dataread('all') #keep reading until we hit one second
#	    time.sleep(.1)
	time.sleep(1)
	vrecord = dataread('all')
	temp = dataread('all')
	while startcode not in temp:
	    temp += dataread('all')
	snip = temp.find(startcode)
	finish = temp[:snip] # get rid of all data  after next start string (including start string)
	temp = temp[snip:]
	vrecord = start + vrecord + finish # Each data[] has a complete start/end, no partial frames
	write(filename,vrecord,vext, 'a')
        write(filename,arecord,aext, 'a')
	arecord = ''
	#thread.start_new_thread(append, (filename,data))
	#append(filename,data)
    return temp

    

def buffer(temp):
    global motion
    display('Starting buffer...')
    #dataread('all')	#clear out the buffer
    step = 0.1
    vbuffer = collections.deque(maxlen=fps*prefault) #not implemented yet, better version of FIFO?
    data = ['']*prefault # Data will be recorded in 1 second intervals and shifted in FIFO style
    while not motion:
	try:
	    #For efficiency, better idea to write data round robin, then figure out how to stitch together
	    #later instead of moving big chuncks of data every second?
	    #update - use collections.deque instead? fps*buffer for size, dump one frame in each?
    	    for y in range(0, prefault-1):	#shift data in FIFO style
		data[y] = data[y+1]		#0=1, 1=2, 2=3, 3=4, etc
	    while startcode not in temp:
		temp = dataread('all')
    	    start = temp[temp.find(startcode):] # get rid of all data before start string
    	    data[prefault-1] = ''
	    ts = time.time()
	    while (time.time()-ts<1): #read one second worth of data
		data[prefault-1] += dataread('line') #keep reading until we hit 1 second
		time.sleep(0.1)
	    temp = dataread('all')
	    while startcode not in temp:
		temp += dataread('all')
	    finish = temp[:temp.find(startcode)] # get rid of all data  after next start string (including start string)
	    temp = temp[temp.find(startcode):]
	    data[prefault-1] = start + data[prefault-1] + finish # Each data[] has a complete start/end, no partial frames
	except KeyboardInterrupt:
	    display('Keyboard Interrupt')
	    cleanup()
    return ''.join(data),temp	#concatenate all data together, pass remaining data back into play

#Instead of building a header from scratch (decoding the spec is too much work), just scrape the header from
#when we first start up and apply that to all subsequent videos created. (header changes based on bitrate/fps/etc)
def getheader():
    data=''
    c = -1
    try:
	while c < 1:
	    data += dataread('all')
	    a = data.find(startcode)+len(startcode) #Finds initial startcode for header
	    b = data[a:].find(startcode)+len(startcode)+a #finds secondary startcode
	    c = data[b:].find(startcode)+b #third startcode should indicate the start of actual video frames
	    display('header')
    except IOError:
        pass
	#TODO - better error handling... exit program if no header can be found as all vids will be corrupt
    data = data[:c] #This is the header
    temp = data[c:] #Pass the remaining bitstream back into play
    return data, temp

#Raw data is dumped to a file with no header (so long recording don't run out of memory)
#After the recording is complete, read data, add header, and overwrite file
#If this is too taxing, this processing could be offloaded to main server
def createwave(filename, extension):
    global a
    filename = '/home/picam/video/'+filename+extension
    wr = open(filename,'r')
    data = wr.read()
    wr.close()

    wf = wave.open(filename, 'wb')
    wf.setnchannels(CHANNELS)
    wf.setsampwidth(a.get_sample_size(FORMAT))
    wf.setframerate(RATE)
    wf.writeframes(data)
    wf.close

def write(filename,media,extension,mode):
    global a
    filename = '/home/picam/video/'+filename+extension
    with open(filename, mode) as file_:
	if mode == 'w':
    	    os.chmod(filename, 0777)
        file_.write(media)

def dataread(amt):
    while True:
        grab = ''
        try:
	    if amt == 'line':
        	grab += bytes(p.stdout.readline())
	    if amt == 'all':
        	grab += bytes(p.stdout.read())
	    if amt == 'flush':
        	grab += bytes(p.stdout.flush())
		break
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
    output = ts+' - '+str(string)
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
    time.sleep(3)
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
    try:
	cpuserial = getserial()
	motion = False
	InitLogger()
	InitPIR()
	main()
    except Exception, e:
	print e.__doc__
	print e.message
	cleanup()

