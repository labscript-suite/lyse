#####################################################################
#                                                                   #
# /traces.py                                                        #
#                                                                   #
# Copyright 2013, Monash University                                 #
#                                                                   #
# This file is part of the program lyse, in the labscript suite     #
# (see http://labscriptsuite.org), and is licensed under the        #
# Simplified BSD License. See the license.txt file in the root of   #
# the project for the full license.                                 #
#                                                                   #
#####################################################################

from __future__ import division
import sys
from pylab import *
from scipy import optimize

def step(t, initial, final, step_time, rise_time):
    exponent = -4*log(3)*(t-step_time)/rise_time
     # make it immune to overflow errors, without sacrificing accuracy:
    exponent[exponent > 700] = 700
    return (final - initial)/(1 + exp(exponent)) + initial
        
def expdecay(t, initial, final, decay_time):
    exponent = -(t-t[0])/decay_time
     # make it immune to overflow errors, without sacrificing accuracy:
    exponent[exponent > 700] = 700
    return (final - initial)*(1 - exp(exponent)) + initial
     
def linear(t, m, c):
    print m,c
    return m*t + c
    
def get_averagem(x,t):
    x = array(x)
    return average(x)
    
def get_stddev(x,t):
    x = array(x)
    return std(x)
    
def fit_step(t,y):
    params, pcov = optimize.curve_fit(step, t, y, [y[0], y[-1], (t[-1]-t[0])/2, (t[-1]-t[0])/10])
    initial, final, step_time, rise_time = params
    d_initial, d_final, d_step_time, d_rise_time = [sqrt(abs(pcov[i,i])) for i in range(pcov.shape[0])] if pcov is not inf else [inf]*4
    return (initial, final, step_time, rise_time), (d_initial, d_final, d_step_time, d_rise_time)

def fit_expdecay(traces,globals_=None, other_results=None, uncertainty=1):
    trace = traces[0]
    t = trace['t']
    y = trace['values']
    params, pcov = optimize.curve_fit(expdecay, t, y, [y[0], y[-1], (t[-1]-t[0])/2], ones(len(t))*uncertainty)
    initial, final, decay_time = params
    d_initial, d_final, d_decay_time = [sqrt(abs(pcov[i,i])) for i in range(pcov.shape[0])] if pcov is not inf else [inf]*3
    reduced_chi2 = (y - expdecay(t,*params))**2/(ones(len(t))*uncertainty**2*(len(t)-3))
    reduced_chi2 = reduced_chi2.sum()
    print 'chi squared per degree of freedom:',  reduced_chi2
    return initial, final, decay_time, d_initial, d_final, d_decay_time
    
def fit_linear(traces,globals_=None, other_results=None):
    trace = traces[0]
    t = array(trace['t'])
    y = array(trace['values'])
    params, pcov = optimize.curve_fit(linear, t-t[0], y, [(y[-1]- y[0])/(t[-1] - t[0]), y[0]])#y[0] - (y[-1]- y[0])/(t[-1] - t[0])*t[0]])
    m,c = params
    d_m, d_c = [sqrt(abs(pcov[i,i])) for i in range(pcov.shape[0])] if pcov is not inf else [inf]*2
    return m,c,d_m,d_c

def expdecay_initial_rate(traces,globals_, other_results):
    initial, final, decay_time, d_initial, d_final, d_decay_time = other_results
    rate = (final-initial)/decay_time
    d_rate = rate * sqrt( (d_initial**2 + d_final**2)/(final-initial)**2 + d_decay_time**2 / decay_time**2)
    return rate, d_rate
    
def step_example():  
    t = linspace(0,10,100000)
    y = zeros(len(t))
    y[t > 5] = 5
    fy = fft(y)
    fy[10:] = 0
    y = ifft(fy).real
    y += normal(size=len(t))

    (initial, final, step_time, rise_time), (d_initial, d_final, d_step_time, d_rise_time) = fit_step(t,y)
    print 'initial = %f +/- %f'%(initial,d_initial)
    print 'final = %f +/- %f'%(final,d_final)
    print 'step_time= %f +/- %f'%(step_time,d_step_time)
    print 'rise_time = %f +/- %f'%(rise_time,d_rise_time)

    plot(t,y)
    plot(t,step(t, initial, final, step_time, rise_time),linewidth = 5)
    grid(True)
    show()
    
def expdecay_example(filepath):  
    import h5py
    f = h5py.File(filepath)
    trace = f['data/traces/motload']
    t = trace['t']
    y = trace['values']
    initial,final,decay_time, d_initial,d_final,d_decay_time = fit_expdecay([trace])
    print 'initial = %f +/- %f'%(initial,d_initial)
    print 'final = %f +/- %f'%(final,d_final)
    print 'step_time= %f +/- %f'%(decay_time,d_decay_time)

    
    plot(t,y)
    plot(t,expdecay(t, initial, final, decay_time),linewidth = 5)
    grid(True)
    show()

def linear_example(filepath):  
    import h5py
    f = h5py.File(filepath)
    trace = f['data/traces/motload']
    t = trace['t']
    y = trace['values']
    m,c,d_m,d_c = fit_linear([trace])
    print 'm = %f +/- %f'%(m,d_m)
    print 'c = %f +/- %f'%(c,d_c)

    
    plot(t,y)
    plot(t,linear(t-t[0],m,c),linewidth = 5)
    grid(True)
    show()    
if __name__ == '__main__': 
    linear_example(sys.argv[1])
