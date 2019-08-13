#!/usr/bin/env python


import numpy as np

import threading

import rospy

from sensor_msgs.msg import JointState, PointCloud2
from intera_core_msgs.msg import JointCommand, IODeviceStatus, IOComponentCommand, EndpointState
from intera_motion_msgs.msg import MotionCommandActionGoal, Trajectory, TrajectoryOptions, Waypoint
from tf2_msgs.msg import TFMessage

import time

from collections import OrderedDict

import rosbag
import datetime

import rospkg

class GRIPPER():
    #edit pull this from sawyer file
    MAX = 0.041667
    MIN = 0.000000

class TOPIC():
    JOINT_STATES = "/{}/joint/states"
    ENDEFF_STATES = "/{}/end_effector/states"

    NAVIGATOR_STATES = "/{}/navigator/states"

    CUFF_STATES = "/{}/cuff/states"
    GRIPPER_STATES = "/{}/gripper/states"

    JOINT_COMMAND = "/{}/joint/command"
    GRIPPER_COMMAND = "/{}/gripper/command"

    MOTION_GOAL = "/{}/motion/goal"

    IO_ROBOT_COMMAND = "/{}/io/robot/command"

    TF = '/tf'

class BUTTON():
    RELEASED = 0
    CLICKED = 1
    HOLDING = 2
    DOUBLE_CLICKED = 3

class DesiredStateProvider(object):

    def __init__(self):

        self._states = OrderedDict()
        self._reached = threading.Event()
        self._reached.clear()
        self._HACK_MODE = JointCommand.POSITION_MODE
        self._cycles_since_active = 0
        self._HACK_MAX_STEPS = 1

    def register_state(self, state_id):
        self._states[state_id] = None

    def get_states(self, state_ids = None):
        if state_ids is None:
            state_ids = self._states.keys()
        elif not isinstance(state_ids, (list, tuple)):
            state_ids = [state_ids]
        return [self._states[state_id] if  state_id in self._states else None for state_id in state_ids]

    def set_states(self, states, state_ids = None):
        if state_ids is None:
            state_ids = self._states.keys()
        elif not isinstance(state_ids, (list, tuple)):
            state_ids = [state_ids]
            states = [states]
        for state_id, state in zip(state_ids, states):
            if state_id in self._states:
                self._states[state_id] = state

    def run_cycle(self):
        self._cycles_since_active += 1

    def activate(self):
        self._cycles_since_active = 0

    def deactivate(self):
        self._cycles_since_active = 0

    def wait_until_reached(self):
        self._reached.clear()
        self._reached.wait()

    def has_reached(self):
        return self._reached.is_set()

    def set_reached(self):
        self._reached.set()

class DesiredStateProviderHandler(object):

    def __init__(self):
        self._des_provider = {}
        self._active_des_provider = None
        self._active_des_provider_lock = threading.Lock()
        self._is_active_held = False

    def register_desired_state_provider(self, name, provider):
        self._des_provider[name] = provider

    def activate_desired_state_provider(self, name):
        if self._active_des_provider != name:
            if not self._is_active_held:
                self._active_des_provider_lock.acquire()
                if self._active_des_provider is not None:
                    states = self._des_provider[self._active_des_provider].deactivate()
                self._active_des_provider = name
                states = self._des_provider[self._active_des_provider].activate()
                self._active_des_provider_lock.release()
            return not self._is_active_held

    def run_cycle(self):
        self._active_des_provider_lock.acquire()
        states = self._des_provider[self._active_des_provider].run_cycle()
        self._active_des_provider_lock.release()

    def get_active_desired_states(self, state_ids=None):
        states = (None,)
        self._active_des_provider_lock.acquire()
        states = self._des_provider[self._active_des_provider].get_states(state_ids)
        self._active_des_provider_lock.release()
        # print(self._active_des_provider)
        # print(self._des_provider[self._active_des_provider]._states)
        return states

    def set_active_desired_states(self, states, state_ids=None):
        self._active_des_provider_lock.acquire()
        self._des_provider[self._active_des_provider].set_states(states,state_ids)
        self._active_des_provider_lock.release()

    def set_desired_states(self, name, states, state_ids=None):
        if self._active_des_provider == name:
            self.set_active_desired_states(states, state_ids)
        else:
            self._des_provider[name].set_states(states,state_ids)

    def wait_until_reached(self):
        self._des_provider[self._active_des_provider].wait_until_reached()

    def set_reached(self):
        self._des_provider[self._active_des_provider].set_reached()

    def hold_active(self):
        self._is_active_held = True

    def release_active(self):
        self._is_active_held = False

class Recorder(object):

    def __init__(self, dir_name = None, prefix = None):

        self.dir_name = '.' if dir_name is None else dir_name
        self.prefix = 'data_' if prefix is None else prefix

        self.max_buffered_recordings = 10
        self.buffered_recordings = []

        self.subs = []


        self._recording_lock = threading.Lock()
        self._is_recording = False

    def add_topic(self, topic, msg):
        sub = rospy.Subscriber(topic, msg, lambda m: self.record(topic, m))
        self.subs.append(sub)

    def is_recording(self):
        return self._is_recording

    def stop_recording(self):
        if not self._is_recording:
            print("Currently not recording")
            return
        self._recording_lock.acquire()
        self._is_recording = False
        self.buffered_recordings[-1].close()
        self._recording_lock.release()

    def start_recording(self, stop_current=True):
        if stop_current:
            self.stop_recording()

        if self._is_recording:
            print("Already recording")
            return
        self._recording_lock.acquire()
        timestamp = datetime.datetime.now().strftime('%Y%m%d%H%M%S')
        bag = rosbag.Bag('{}/{}_{}.bag'.format(self.dir_name,self.prefix, timestamp), 'w', compression='bz2')
        if len(self.buffered_recordings) == self.max_buffered_recordings:
            del self.buffered_recordings[0]
        self.buffered_recordings.append(bag)

        self._is_recording = True
        self._recording_lock.release()

    def record(self, topic, msg):
        if self._is_recording:
            # print(self.buffered_recordings)
            if self.buffered_recordings:
                self._recording_lock.acquire()
                try:
                    self.buffered_recordings[-1].write(topic, msg)
                except ValueError as err:
                    print(err)
                self._recording_lock.release()


class Sawyer(DesiredStateProviderHandler):

    def __init__(self, name, data_dir = None):

        DesiredStateProviderHandler.__init__(self)

        self._name = name

        self._joint_names = ["right_j0", "right_j1", "right_j2", "right_j3", "right_j4", "right_j5", "right_j6"]

        self._ctl_freq = 100
        self._ctl_thread = threading.Thread(target=self._ctl_loop)

        self._joint_command_pub = rospy.Publisher(TOPIC.JOINT_COMMAND.format(self._name), JointCommand, queue_size=10)
        self._gripper_command_pub = rospy.Publisher(TOPIC.GRIPPER_COMMAND.format(self._name), IOComponentCommand, queue_size=10)
        self._motion_goal_pub = rospy.Publisher(TOPIC.MOTION_GOAL.format(self._name), MotionCommandActionGoal, queue_size=10)
        self._io_robot_pub = rospy.Publisher(TOPIC.IO_ROBOT_COMMAND.format(self._name), IOComponentCommand, queue_size=10)


        self._joint_states_sub = rospy.Subscriber(TOPIC.JOINT_STATES.format(self._name), JointState, self._joint_states_cb)
        self._gripper_states_sub = rospy.Subscriber(TOPIC.GRIPPER_STATES.format(self._name), IODeviceStatus, self._gripper_states_cb)
        self._navigator_states_sub = rospy.Subscriber(TOPIC.NAVIGATOR_STATES.format(self._name), IODeviceStatus, self._navigator_states_cb)
        self._cuff_states_sub = rospy.Subscriber(TOPIC.CUFF_STATES.format(self._name), IODeviceStatus, self._cuff_states_cb)


        self._joint_state = None
        self._gripper_state = None
        self._gripper_state_set = None

        self._joint_home = np.array([
            [-0.345591796875, -1.0379169921875, -3.0447080078125, -1.6942548828125, -0.0912578125, -0.7949296875, 1.62],
            [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0],
            [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]
        ])

        self._gripper_home = GRIPPER.MAX

        idle = DesiredStateProvider()
        idle.register_state('gripper')
        self.register_desired_state_provider('idle', idle)

        goto = DesiredStateProvider()
        goto.register_state('joint')
        goto.register_state('gripper')
        goto._HACK_MODE = JointCommand.VELOCITY_MODE
        self.register_desired_state_provider('goto', goto)

        self.activate_desired_state_provider('idle')

        self._state_callbacks = []

        self.HACK_was_holding = False
        self.HACK_do_print = False

        self.recorder = None
        if self._name == 'anna':
            self.recorder = Recorder(dir_name=data_dir)
            # self.recorder = Recorder(dir_name='{}/../../data'.format(rospkg.RosPack().get_path('fri_base')))
            # # self.recorder = Recorder(dir_name='data')
            self.recorder.add_topic(TOPIC.JOINT_STATES.format('anna'), JointState)
            self.recorder.add_topic(TOPIC.JOINT_STATES.format('alexei'), JointState)
            self.recorder.add_topic(TOPIC.ENDEFF_STATES.format('anna'), EndpointState)
            self.recorder.add_topic(TOPIC.ENDEFF_STATES.format('alexei'), EndpointState)
            # self.recorder.add_topic(TOPIC.TF, TFMessage)
            # self.recorder.add_topic("/kinect1/qhd/points/", PointCloud2)

    def start_recording(self):
        if self.recorder:
            self.recorder.start_recording()

    def is_recording(self):
        if self.recorder:
            return self.recorder.is_recording()
        return False

    def stop_recording(self):
        if self.recorder:
            self.recorder.stop_recording()


    def register_state_cb(self, cb):
        self._state_callbacks.append(cb)

    def _joint_states_cb(self, msg):

        if 'right_j0' in msg.name:
            self._joint_state = np.array([msg.position[1:8], msg.velocity[1:8], [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]])
            for cb in self._state_callbacks:
                cb(self._joint_state,'joint')

    def _gripper_states_cb(self, msg):
        for signal in msg.signals:
            if signal.name == "position_response_m":
                self._gripper_state = float(signal.data[1:-1])
            elif signal.name == "position_m":
                self._gripper_state_set = float(signal.data[1:-1])
                for cb in self._state_callbacks:
                    cb(self._gripper_state_set,'gripper')

    def _navigator_states_cb(self, msg):
        for signal in msg.signals:
            if signal.name == "right_button_triangle":
                self.nav_action_x(int(signal.data[1]))
            elif signal.name == "right_button_square":
                self.nav_action_square(int(signal.data[1]))
            elif signal.name == "right_button_circle":
                self.nav_action_circle(int(signal.data[1]))
            elif signal.name == "right_button_ok":
                self.nav_action_ok(int(signal.data[1]))
            elif signal.name == "right_button_back":
                self.nav_action_back(int(signal.data[1]))
            elif signal.name == "right_button_show":
                self.nav_action_rethink(int(signal.data[1]))

    def _cuff_states_cb(self, msg):
        for signal in msg.signals:
            if signal.name == "right_button_lower":
                self.cuff_action_circle(int(signal.data[1]))
            elif signal.name == "right_button_upper":
                self.cuff_action_bar(int(signal.data[1]))
            elif signal.name == "right_cuff":
                self.cuff_action_pad(int(signal.data[1]))

    def _calibrate_gripper(self):
        gripper_calibration_msg = IOComponentCommand()
        gripper_calibration_msg.time = rospy.Time.now()
        gripper_calibration_msg.op = "set"
        gripper_calibration_msg.args = '{"signals": {"calibrate": {"data": [true], "format": {"type": "bool"}}}}'
        self._gripper_command_pub.publish(gripper_calibration_msg)

    def start(self):
        time.sleep(0.1)
        self._turn_off_lights()
        self._calibrate_gripper()
        self._ctl_thread.start()

    def go_home(self):
        self.goto_position(self._joint_home, self._gripper_home)

    def goto_position(self, joints=None, gripper=None):
        if joints is not None:
            if gripper is not None:
                self.set_desired_states('goto', (joints, gripper), ('joint','gripper'))
            else:
                self.set_desired_states('goto', (joints,), ('joint',))
            self.activate_desired_state_provider('goto')
        else:
            if gripper is not None:
                self.set_desired_states('goto', (gripper,), ('gripper',))
                self.activate_desired_state_provider('goto')


    def _ctl_loop(self):
        rate = rospy.Rate(self._ctl_freq)
        #
        # trajectory_options = TrajectoryOptions()
        # trajectory_options.interaction_control = False
        # trajectory_options.interpolation_type = TrajectoryOptions.JOINT


        last_vel = np.array([0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0])
        last_acc = np.array([0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0])


        rec_data = []
        rec_seq = 0




        def compute_vel(measured, desired):

            tol = 0.008

            mp = measured[0,:]
            dp = desired[0,:]
            mv = measured[1,:]
            dv = desired[1,:]

            cycle = self._des_provider[self._active_des_provider]._cycles_since_active


            if cycle == 1:
                max_steps = (3.5*np.abs(dp-mp)*self._ctl_freq+50).astype(np.int)
                max_steps[np.abs(dp-mp) < tol] = 1
                self._des_provider[self._active_des_provider]._HACK_MAX_STEPS = max_steps
            else:
                max_steps = self._des_provider[self._active_des_provider]._HACK_MAX_STEPS

            # max_steps = 500

            steps = np.minimum(cycle, max_steps).astype(np.float64)
            weights = (steps/max_steps)

            dgain = 0.0
            pgain = 4.0

            ep = (mp*(1.0-weights)+weights*dp)-mp
            ep[np.abs(dp-mp) < tol] = 0.0

            ev = dv-(mv*(1.0-weights)+weights*dv)

            sv =  pgain*ep + dgain*ev
            sv = np.maximum(np.minimum(sv,4.0),-4.0)

            return sv



        while not rospy.is_shutdown():

            des_joint_state, des_gripper_state = self.get_active_desired_states(state_ids=('joint','gripper'))
            self.run_cycle()

            timestamp = rospy.Time.now()

            if des_joint_state is not None:


                MODE = self._des_provider[self._active_des_provider]._HACK_MODE

                joint_msg =  JointCommand()
                joint_msg.header.stamp = timestamp
                joint_msg.names = self._joint_names

                acc = (self._joint_state[1,:]-last_vel)/0.01
                jerk = (acc-last_acc)/0.01

                vel = compute_vel(self._joint_state, des_joint_state)
                # if self._name == 'alexei':
                #     with np.printoptions(precision=6, suppress=True, floatmode='fixed', sign=' '):
                #         print(self._joint_state[0,:])
                #         print(des_joint_state[0,:])
                #         print(self._joint_state[1,:])
                #         print(des_joint_state[1,:])
                #         print(vel)
                #         print(self._des_provider[self._active_des_provider]._cycles_since_active)
                #         print(self._des_provider[self._active_des_provider]._HACK_MAX_STEPS)
                #         print("")

                if MODE == JointCommand.VELOCITY_MODE:


                    # if np.any(np.abs(vel[-1]) > 10.0):
                    #     print("pos msr:{} des:{}\tvel msr:{} des:{}\tset: {} acc: {} jerk: {}".format(self._joint_state[0,-1],des_joint_state[0,-1],self._joint_state[1,-1],des_joint_state[1,-1],vel[-1],acc[-1], jerk[-1]))
                    if self.HACK_do_print:
                        # print("pos msr:{} des:{}\tvel msr:{} des:{}\tset: {} acc: {} jerk: {}".format(self._joint_state[0,-1],des_joint_state[0,-1],self._joint_state[1,-1],des_joint_state[1,-1],vel[-1],acc[-1], jerk[-1]))
                        with np.printoptions(precision=6, suppress=True, floatmode='fixed', sign=' '):
                            # print("pm:{},\npd:{},\nvm:{},\nvd:{},\nvs:{},".format(np.array2string(self._joint_state[0,:]),np.array2string(des_joint_state[0,:]),np.array2string(self._joint_state[1,:]),np.array2string(des_joint_state[1,:]),np.array2string(vel)))
                            # print(np.array2string(np.array([self._joint_state[0,:], des_joint_state[0,:], self._joint_state[1,:], des_joint_state[1,:], vel])))
                            rec_data.append(np.array([self._joint_state[0,:], des_joint_state[0,:], self._joint_state[1,:], des_joint_state[1,:], vel]))


                    joint_msg.mode = JointCommand.VELOCITY_MODE
                    joint_msg.position = []
                    joint_msg.velocity = vel
                    joint_msg.acceleration = []
                    joint_msg.effort = []


                elif MODE == JointCommand.POSITION_MODE:

                    print("NOOOOO:(")

                    joint_msg.mode = JointCommand.POSITION_MODE
                    joint_msg.position = des_joint_state[0,:]
                    joint_msg.velocity = []
                    joint_msg.acceleration = []
                    joint_msg.effort = []

                self._joint_command_pub.publish(joint_msg)

                if self._joint_state is not None and np.all(np.abs(self._joint_state[0,:]-des_joint_state[0,:])<0.008):
                    self.set_reached()

                    if self.HACK_do_print:
                        self.HACK_do_print = False
                        # np.save('data_{}_{:03d}'.format(self._name,rec_seq),np.array(rec_data))
                        rec_seq += 1
                        rec_data = []

                last_vel = self._joint_state[1,:]
                last_acc = acc

            if des_gripper_state is not None:
                gripper_msg = IOComponentCommand()
                gripper_msg.time = timestamp
                gripper_msg.op = "set"
                gripper_msg.args = '{"signals": {"position_m": {"data": ['+str(des_gripper_state)+'], "format": {"type": "float"}}}}'
                self._gripper_command_pub.publish(gripper_msg)



            rate.sleep()

    def _turn_off_lights(self):
        light_msg = IOComponentCommand()
        light_msg.time = rospy.Time.now()
        light_msg.op = "set"
        light_msg.args = '{"signals": {"head_red_light": {"data": [false], "format": {"type": "bool"}}}}'
        self._io_robot_pub.publish(light_msg)

    def nav_action_x(self, action):
        if action == BUTTON.CLICKED:
            print("CLICKED X")

    def nav_action_back(self, action):
        if action == BUTTON.CLICKED:
            print("CLICKED back")

    def nav_action_ok(self, action):
        if action == BUTTON.CLICKED:
            print("CLICKED wheel")

    def nav_action_square(self, action):
        # if action == BUTTON.CLICKED:
        #     self.go_home()
        # elif action == BUTTON.DOUBLE_CLICKED:
        #     print("GOTO HOME")
        if action != BUTTON.RELEASED:
            self.go_home()

    def nav_action_circle(self, action):
        if action != BUTTON.RELEASED:

            is_recording = self.is_recording()

            if is_recording:
                self.stop_recording()
            else:
                self.start_recording()

            is_recording = self.is_recording()
            print("RECORDING: {}".format(is_recording))
            light_msg = IOComponentCommand()
            light_msg.time = rospy.Time.now()
            light_msg.op = "set"
            light_msg.args = '{"signals": {"head_red_light": {"data": ['+str(is_recording).lower()+'], "format": {"type": "bool"}}}}'
            self._io_robot_pub.publish(light_msg)

    def nav_action_rethink(self, action):
        if action != BUTTON.RELEASED:
            if 'shadowing' in self._des_provider:
                if self._active_des_provider == 'shadowing':
                    self.activate_desired_state_provider('idle')
                else:
                    self.activate_desired_state_provider('shadowing')

    def cuff_action_pad(self, action):

        if action != BUTTON.RELEASED:
            self.HACK_was_holding = True
            if self._active_des_provider != 'shadowing':
                self.activate_desired_state_provider('idle')
        elif self.HACK_was_holding:
                self.HACK_was_holding = False
                print("RELEASE {}".format(self._name))
                self.HACK_do_print = True

    def cuff_action_circle(self, action):
        # if action == BUTTON.CLICKED:
        if action != BUTTON.RELEASED and action != BUTTON.HOLDING:
            if self._gripper_state_set == GRIPPER.MAX:
                self.set_active_desired_states(GRIPPER.MIN, 'gripper')
            elif self._gripper_state_set == GRIPPER.MIN:
                self.set_active_desired_states(GRIPPER.MAX, 'gripper')

    def cuff_action_bar(self, action):
        self.cuff_action_circle(action)
