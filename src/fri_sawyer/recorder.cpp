
#include "ros/ros.h"
#include "std_msgs/Bool.h"

#include "fri_sawyer/CreateRecorderBond.h"
#include "fri_sawyer/CommandRecorderBond.h"


#include "rosbag/bag.h"
#include <thread>
#include <bondccp/bond.h>
#include <vector>

//
// from fri_sawyer.srv import CreateRecorderBond, CreateRecorderBondRequest, CreateRecorderBondResponse, CommandRecorderBond, CommandRecorderBondRequest, CommandRecorderBondResponse
//
// import rosbag
// import threading
// import datetime
// from bondpy import bondpy
//
// import uuid


//  ##############
// GENERATING UUID: CREDIT GOES TO  https://lowrey.me/guid-generation-in-c-11/

#include <sstream>
#include <random>
#include <string>

unsigned int random_char() {
    std::random_device rd;
    std::mt19937 gen(rd());
    std::uniform_int_distribution<> dis(0, 255);
    return dis(gen);
}

std::string generate_hex(const unsigned int len) {
    std::stringstream ss;
    for (auto i = 0; i < len; i++) {
        const auto rc = random_char();
        std::stringstream hexstream;
        hexstream << std::hex << rc;
        auto hex = hexstream.str();
        ss << (hex.length() < 2 ? '0' + hex : hex);
    }
    return ss.str();
}

// ##############################################



class Recorder{
private:
  const ros::NodeHandle nh;
  const std::string dir_name;
  const std::string prefix;
  const std::vector<std::string> topics;
  const std::vector<???> msg_types;
  std::vector<ros::Subscriber> sub_threads;
  rosbag::Bag *bag;

public:

  Recorder(const ros::NodeHandle &nh, const std::string &dir_name, const std::string &prefix, const std::vector<std::string> &topics,  const std::vector<???> &msg_types)
    : nh(nh), dir_name(dir_name), prefix(prefix), topics(topics), msg_types(msg_types)
  {

        self.bag_lock = threading.Lock()

        // this->subs.resize(topics.size())

        for (int idx = 0; idx < topics.size(); idx++){
          this->subs.push_back(nh.subscribe(topics[idx],1000,));
        }

        for topic, msg_type in zip(topics, msg_types):
            # print("ADDING SUBSCRIBER {}:{}".format(topic, msg_type))
            self.subs.append(rospy.Subscriber(topic, msg_type, lambda msg, std::bind(this->, _3) topic=topic: self.record(topic, msg)))

        self.open_srv = rospy.Service('start_recording', CommandRecorderBond, self.handle_open)
        self.close_srv = rospy.Service('stop_recording', CommandRecorderBond, self.handle_close)
        self.bond_srv = rospy.Service('bond_recording', CreateRecorderBond, self.handle_bond)
        self.is_recording_srv = rospy.Service('is_recording', CommandRecorderBond, self.handle_is_recording)

        self.bonds = {}
        self.current_bond = {}
  }
  ~Recorder()
  {
  }

}



    def start_bond(self, bond_id):
        bond = bondpy.Bond("/recorder_bond", bond_id)
        bond.start()
        if not bond.wait_until_formed(rospy.Duration(10.0)):
            raise Exception('Bond could not be formed')
        bond.wait_until_broken()
        if self.current_bond == bond_id:
            self.close()

    def record(self, topic, msg):
        # print("NEW DATA: {}:{}".format(topic, type(msg)))
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
                # self.bag = rosbag.Bag('{}/{}_{}.bag'.format(self.dir_name,self.prefix, timestamp), 'w')
                self.current_bond = bond_id

    def close(self):
        with self.bag_lock:
            if self.bag is not None:
                self.bag.close()
                self.current_bond = None
                self.bag = None
