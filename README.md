PiSecurityCam
=============

Local video creation with prebuffer using PIR/GPIO or sockets.  Motion detection through image
processing is not within the scope of this project.

Intended application will be multiple Pi's recording autonomously, then interacting with a common
server for archive/presentation to the user.  Server will do any heavy encoding/processing as required.

Output of this program can by piped to another for both security cam recording as well as streaming.

After initialization, program waits in prefault buffer while loop waiting for trigger
Prefault operates in 1 second blocks with FIFO functionality.
When trigger picks up, prefault is written to file, then moves to live recording
live recording appends to the file once per second until motion subsides (including any retriggers)
Once motion subsides, video continues to be appended in 1 second intervals for postfault time.
For example, a 5s prefault, 3s motion, and 10s postfault would result in 18 seconds of video minimum plus
intermediate file recording time (due to stdout buffer), plus any "rounding up" if motion
stops in the middle of a 1 second recording block
After event concludes, system returns to prefault buffer.
