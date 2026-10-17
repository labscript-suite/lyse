from lyse import Run, data, path
import matplotlib.pyplot as plt

# Let's obtain our data for this shot -- globals, image attributes and
# the results of any previously run single-shot routines:
ser = data(path)

# Get a global called x:
x = ser['x']

# Get a result saved by another single-shot analysis routine which has
# already run. The result is called 'y', and the routine was called
# 'some_routine':
y = ser['some_routine','y']

# Image attributes are also stored in this series:
w_x2 = ser['side','absorption','OD','Gaussian_XW']

# If we want actual measurement data, we'll have to instantiate a Run object:
run = Run(path)

# Obtaining a trace:
t, mot_fluorecence = run.get_trace('mot fluorecence')

# Now we might do some analysis on this data. Say we've written a
# linear fit function (or we're calling some other libaries linear
# fit function):
m, c = linear_fit(t, mot_fluorecence)

# We might wish to plot the fit on the trace to show whether the fit is any good:

plt.plot(t,mot_fluorecence,label='data')
plt.plot(t,m*t + x,label='linear fit')
plt.xlabel('time')
plt.ylabel('MOT flourescence')
plt.legend()

# Don't call show() ! lyse will introspect what figures have been made
# and display them once this script has finished running.  If you call
# show() it won't find anything. lyse keeps track of figures so that new
# figures replace old ones, rather than you getting new window popping
# up every time your script runs.

# We might wish to save this result so that we can compare it across
# shots in a multishot analysis:
run.save_result('mot loadrate', c)