
import rospy

from std_msgs.msg import Bool
from fri_sawyer.srv import CreateRecorderBond, CreateRecorderBondRequest, CreateRecorderBondResponse, CommandRecorderBond, CommandRecorderBondRequest, CommandRecorderBondResponse

import rosbag
import threading
import datetime
from bondpy import bondpy

import uuid

class Recorder(object):

    def __init__(self, dir_name, prefix, topics, msg_types):

        self.dir_name = dir_name
        self.prefix = prefix
        self.bag = None
        self.bag_lock = threading.Lock()
        self.subs = []
        for topic, msg_type in zip(topics, msg_types):
            self.subs.append(rospy.Subscriber(topic, msg_type, lambda msg: self.record(topic, msg)))

        self.open_srv = rospy.Service('start_recording', CommandRecorderBond, self.handle_open)
        self.close_srv = rospy.Service('stop_recording', CommandRecorderBond, self.handle_close)
        self.bond_srv = rospy.Service('bond_recording', CreateRecorderBond, self.handle_bond)
        self.is_recording_srv = rospy.Service('is_recording', CommandRecorderBond, self.handle_is_recording)

        self.bonds = {}
        self.current_bond = {}

    def start_bond(self, bond_id):
        bond = bondpy.Bond("/recorder_bond", bond_id)
        bond.start()
        if not bond.wait_until_formed(rospy.Duration(3.0)):
            raise Exception('Bond could not be formed')
        bond.wait_until_broken()
        if self.current_bond == bond_id:
            self.close()

    def record(self, topic, msg):
        with self.bag_lock:
            if self.bag is not None:
                self.bag.write(topic, msg)

    def handle_bond(self, req):
        bond_id = str(uuid.uuid4())
        bond_thread = threading.Thread(target=self.start_bond, args=(bond_id,))
        self.bonds[bond_id] = bond_thread
        resp = CreateRecorderBondResponse()
        resp.bond_id = bond_id

        bond_thread.start()
        return resp

    def handle_open(self, req):
        if req.bond_id not in self.bonds:
            raise RuntimeError("Unknown bond id: {}".format(req.bond_id))
        self.open(req.bond_id)
        return self.handle_is_recording(req)

    def handle_is_recording(self, req):
        with self.bag_lock:
            resp = CommandRecorderBondResponse()
            resp.is_recording = (self.bag is not None)
            return resp

    def handle_close(self, req):
        if req.bond_id not in self.bonds:
            raise RuntimeError("Unknown bond id: {}".format(req.bond_id))
        self.close()
        return self.handle_is_recording(req)

    def open(self, bond_id):
        with self.bag_lock:
            if self.bag is None:
                timestamp = datetime.datetime.now().strftime('%Y%m%d%H%M%S')
                self.bag = rosbag.Bag('{}/{}_{}.bag'.format(self.dir_name,self.prefix, timestamp), 'w', compression='bz2')
                self.current_bond = bond_id

    def close(self):
        with self.bag_lock:
            if self.bag is not None:
                self.bag.close()
                self.current_bond = None
                self.bag = None




class RecorderRemote(object):

    def __init__(self, prefix = None):
        rospy.wait_for_service('/bond_recording', 2.0)
        self.bond_recording_service = rospy.ServiceProxy('/bond_recording', CreateRecorderBond)

        rospy.wait_for_service('/start_recording', 2.0)
        self.start_recording_service = rospy.ServiceProxy('/start_recording', CommandRecorderBond)

        rospy.wait_for_service('/stop_recording', 2.0)
        self.stop_recording_service = rospy.ServiceProxy('/stop_recording', CommandRecorderBond)

        rospy.wait_for_service('/is_recording', 2.0)
        self.is_recording_service = rospy.ServiceProxy('/is_recording', CommandRecorderBond)

        self.bond = None
        self.bond_id = None
        self.create_bond()

        self._is_recording = False
        self._update_is_recording()

    def _update_is_recording(self):
        try:
            req = CommandRecorderBondRequest();
            req.bond_id = self.bond_id
            resp = self.is_recording_service(req)
            self._is_recording = resp.is_recording
        except rospy.ServiceException as exc:
            print("Service did not process request: " + str(exc))

    def is_recording(self):
        return self._is_recording

    def create_bond(self):
        try:
            resp = self.bond_recording_service(CreateRecorderBondRequest())
            bond_id = resp.bond_id
            self.bond = bondpy.Bond("/recorder_bond", bond_id)
            self.bond.start()
            if not self.bond.wait_until_formed(rospy.Duration(3.0)):
                raise Exception('Bond could not be formed')
            self.bond_id = bond_id
        except rospy.ServiceException as exc:
            print("Service did not process request: " + str(exc))


    def stop_recording(self):
        try:
            req = CommandRecorderBondRequest();
            req.bond_id = self.bond_id
            resp = self.stop_recording_service(req)
            self._is_recording = resp.is_recording
        except rospy.ServiceException as exc:
            print("Service did not process request: " + str(exc))

    def start_recording(self, stop_current=True):
        if stop_current:
            self.stop_recording()

        try:
            req = CommandRecorderBondRequest();
            req.bond_id = self.bond_id
            resp = self.start_recording_service(req)
            self._is_recording = resp.is_recording
        except rospy.ServiceException as exc:
            print("Service did not process request: " + str(exc))
