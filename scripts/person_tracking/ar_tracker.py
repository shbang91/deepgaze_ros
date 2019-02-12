#!/usr/bin/env python

# ROS version written by Minkyu Kim

#The MIT License (MIT)
#Copyright (c) 2016 Massimiliano Patacchiola
#
#THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF 
#MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY 
#CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE 
#SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.

#In this example the Particle Filter is used in order to stabilise some noisy detection.
#The Backprojection algorithm is used in order to find the pixels that have the same HSV 
#histogram of a predefined template. The template is a subframe of the main image or an external
#matrix that can be used as a filter. We track the object taking the contour with the largest area
#returned by a binary mask (blue rectangle). The center of the contour is the tracked point. 
#To test the Particle Filter we inject noise in the measurements returned by the Backprojection. 
#The Filter can absorbe the noisy measurements, giving a stable estimation of the target center (green dot).

#COLOR CODE:

import cv2
import roslib
import sys
import rospy
import random
import math
import numpy as np
from numpy.random import uniform
from cv_bridge import CvBridge, CvBridgeError
import select, termios, tty
from sensor_msgs.msg import Image
from sensor_msgs.msg import PointCloud2
from sensor_msgs.msg import JointState
import sensor_msgs.point_cloud2 as pcl2

from nav_msgs.msg import OccupancyGrid
from geometry_msgs.msg import PoseArray
from geometry_msgs.msg import PoseStamped
from geometry_msgs.msg import PointStamped
from geometry_msgs.msg import Pose
from geometry_msgs.msg import Point
from geometry_msgs.msg import Quaternion
from people_msgs.msg import PositionMeasurementArray
from std_msgs.msg import String
import std_msgs.msg
from os import listdir
import os
import tf
from tf import TransformListener
from tf.transformations import euler_from_quaternion, quaternion_from_euler
from octomap_msgs.msg import Octomap
import octomap_msgs
# import octomap
# from octomap import OcTreeBase, OcTree

# import tensorflow as tf

# from deepgaze.color_detection import BackProjectionColorDetector
# from deepgaze.mask_analysis import BinaryMaskAnalyser
from deepgaze.object3d_tracking import ParticleFilter
# from deepgaze.motion_tracking import ParticleFilter

class ArTracker(object):
    def __init__(self, wait=0.0):

        tot_particles =200
        self.std=0.1;
        self.my_particle = ParticleFilter(10, 10, tot_particles)
        self.context_particle = ParticleFilter(10,10,tot_particles)
        self.noise_probability = 0.10 #in range [0, 1.0]
        self.robot_pose=np.zeros((5,1))
        self.map_received=False
        self.dynamic_map=OccupancyGrid()
        self.static_map=OccupancyGrid()

        self.estimated_point_pub=rospy.Publisher("/estimated_target",PoseStamped,queue_size=30)
        self.avg_point_pub=rospy.Publisher("/avg_point",PoseStamped,queue_size=30)


        #Declare for subscribing rostopics
        posearray_topic="/ar_tracker_measurements"
	rospy.Subscriber(posearray_topic, PositionMeasurementArray, self.PositionMeasurementCb)
        robot_pose_topic='global_pose'
	rospy.Subscriber(robot_pose_topic, PoseStamped, self.robot_pose_Cb)
        jointstates_topic='hsrb/joint_states'
	rospy.Subscriber(jointstates_topic, JointState, self.joint_state_Cb)
        clicked_point_topic="/clicked_point"
	rospy.Subscriber(clicked_point_topic, PointStamped, self.click_point_Cb)
        octomap_topic="/octomap_binary"
	# rospy.Subscriber(octomap_topic, Octomap, self.octomap_Cb)
        
        map_topic="/projected_map"
        rospy.Subscriber(map_topic, OccupancyGrid, self.map_Cb)
        
        staticmap_topic="/static_obstacle_map_ref"
        rospy.Subscriber(staticmap_topic, OccupancyGrid, self.staticmap_Cb)
 

        #Declare for publishing rostopics
        self.pcl_pub=rospy.Publisher("/particle_samples",PointCloud2,queue_size=50)
        self.pcl_context_pub=rospy.Publisher("/particle_samples_context",PointCloud2,queue_size=50)
        self.pcl_fov_pub=rospy.Publisher("/particle_samples_fov",PointCloud2,queue_size=50)

        #Initialization of variables
        self.context_category=1
        self.search_mode=0
        self.last_callbacktime=rospy.get_time()
        self.context_activate=0
        self.duration=0
        self.counter=0
        self.target_z=0
        self.avg_x=0
        self.avg_y=0

    # def octomap_Cb(self,msg):
        # rospy.loginfo("octomap callback")
        # tree =octomap_msgs.msg.msgToMap(msg)
        # octree=

    def staticmap_Cb(self,msg):
        self.static_map = msg
        self.map_received=True
        
    def map_Cb(self,msg):
        self.dynamic_map = msg
        # rospy.loginfo("origin x: %.2lf, y: %.2lf, resolution: %.2lf", self.dynamic_map.info.origin.position.x
                      # ,self.dynamic_map.info.origin.position.y,self.dynamic_map.info.resolution)




    def check_obsb(self,pos_x,pos_y):

        map_idx=self.Coord2CellNum_STATIC(pos_x,pos_y)
        # rospy.loginfo("map idx : %d ", map_idx )

        if(self.static_map.data[map_idx]>20):
            return True
        else:
            return False

    def Coord2CellNum(self, pos_x, pos_y):
        target_Coord=[]

        reference_origin_x=self.dynamic_map.info.origin.position.x
        reference_origin_y=self.dynamic_map.info.origin.position.y

        temp_x = pos_x-reference_origin_x
        temp_y = pos_y-reference_origin_y

        target_Coord_x= int(temp_x/self.dynamic_map.info.resolution)
        target_Coord_y= int(temp_y/self.dynamic_map.info.resolution)

        index= target_Coord_x+self.dynamic_map.info.width*target_Coord_y
        return index

    def Coord2CellNum_STATIC(self, pos_x, pos_y):
        target_Coord=[]

        reference_origin_x=self.static_map.info.origin.position.x
        reference_origin_y=self.static_map.info.origin.position.y

        temp_x = pos_x-reference_origin_x
        temp_y = pos_y-reference_origin_y

        target_Coord_x= int(temp_x/self.static_map.info.resolution)
        target_Coord_y= int(temp_y/self.static_map.info.resolution)

        index= target_Coord_x+self.static_map.info.width*target_Coord_y
        return index

    def getlinevalue(self, line_type, input_x, input_y ):
        head_angle = self.robot_pose[2]+self.robot_pose[3]
        theta_1= self.robot_pose[2]+self.robot_pose[3]-29*math.pi/180.0
        theta_2= self.robot_pose[2]+self.robot_pose[3]+29*math.pi/180.0
        slope1 = math.tan(theta_1)
        slope2 = math.tan(theta_2)
        mode=0
        
        coeff_sign=-1.0

        if (theta_1 < -math.pi/2.0) & (theta_2 > -math.pi/2.0):
            temp=slope2
            mode=1
        elif (theta_1 < math.pi/2.0) & (theta_2 > math.pi/2.0):
            mode=2
        elif (theta_1 < -math.pi/2.0) & (theta_2 < -math.pi/2.0):
            mode=5
        elif  (theta_2 < -math.pi/2.0):
            mode=3
        elif (theta_1 > math.pi/2) & (theta_2 > math.pi/2.0):
            mode=4

        if line_type==1:
            slope=slope1
            if mode==0:
               coeff_sign=-1.0
            elif mode==1:
               coeff_sign=1.0
            elif mode==2:
               coeff_sign=-1.0  
            elif mode==4:
               coeff_sign=1.0 
            elif mode==5:
               coeff_sign=1.0

        elif line_type ==2:
            slope=slope2
            if mode==1:
                coeff_sign=1.0;
            elif mode==0:
                coeff_sign=1.0 
            elif mode==3:
                coeff_sign=1.0
        else:
            rospy.loginfo("linetype is wrong")

        res= slope*(input_x-self.robot_pose[0])+self.robot_pose[1]-input_y;

        if res*coeff_sign > 0 :
            return True
        else:
            return False



    def Is_inFOV_point(self, point):

        res1=self.getlinevalue(1,point.x, point.y)
        res2=self.getlinevalue(2,point.x, point.y)

        if res1 & res2:
            return True
        else:
            return False
    
    
    def Is_inFOV(self, point_x, point_y):

        res1=self.getlinevalue(1,point_x, point_y)
        res2=self.getlinevalue(2,point_x, point_y)
        if res1 & res2:
            return True
        else:
            return False
    
    

    def click_point_Cb(self,msg):
        rospy.loginfo("clicked_point")
        result = self.Is_inFOV_point(msg.point)
        rospy.loginfo("fov test result: %d  ",result)



    def robot_pose_Cb(self, msg):
        self.robot_pose[0]=msg.pose.position.x
        self.robot_pose[1]=msg.pose.position.y
        robot_orientation=msg.pose.orientation

        #get yaw angle from quarternion
        orientation_list=[robot_orientation.x, robot_orientation.y, robot_orientation.z,robot_orientation.w]
        roll,pitch,yaw=euler_from_quaternion(orientation_list)
        self.robot_pose[2]=yaw
        # robot_yaw=msg.:


    def joint_state_Cb(self, msg):
        self.robot_pose[3]=msg.position[9]
        self.robot_pose[4]=msg.position[10]
        # rospy.loginfo("tilt joint: %.2lf",self.robot_pose[3])

        

    def Estimate_Filter(self):
        #Predict the position of the target
        self.my_particle.predict(x_velocity=0.00, y_velocity=0.00, std=self.std)
        self.context_particle.predict(x_velocity=0.00, y_velocity=0.00, std=self.std)

        # rospy.loginfo("searchmode : %d ",self.search_mode)
        # if self.search_mode==1 & self.context_activate==0:
        if self.search_mode==1:
            if self.counter<2000:
                self.context_particle.predict_context(self.context_category, std=self.std)
            else:
                self.context_particle.predict(x_velocity=0.05, y_velocity=0.05, std=self.std)

            # self.counter=self.counter+1
            # if self.counter==2000:
                # self.search_mode=0
                # self.counter=0;

            # self.context_activate=1
        # elif self.search_mode==1 & self.context_activate==1:
            # self.context_particle.predict(x_velocity=0.00, y_velocity=0.00, std=self.std)
        # else:
        # self.context_particle.predict(x_velocity=0.05, y_velocity=0.05, std=self.std)
            # print "nothing"
        

        #Drawing the particles.
        # self.visualizeparticles()
        # self.my_particle.drawParticles(frame)

        #Estimate the next position using the internal model
        x_estimated, y_estimated, _, _ = self.my_particle.estimate()
        context_x_estimated, context_y_estimated, _, _ = self.context_particle.estimate()

        #publish 
        # est_pose=PoseStamped()
        # est_pose.pose=Pose(Point(context_x_estimated,context_y_estimated,0.5),Quaternion(0,0,0,1))
        # est_pose.header.stamp=rospy.Time.now()
        # est_pose.header.frame_id='map'
        # self.estimated_point_pub.publish(est_pose)

        # current_entropy=self.my_particle.get_entropy()
        # rospy.loginfo("estimated particle : "+str(current_entropy))
        # cv2.circle(frame, (x_estimated, y_estimated), 3, [0,255,0], 5) #GREEN dot

    def filter_by_Occgrids(self):
        #based on projected_map from octomap
        #remove partilces from partilces false-postive
        if self.map_received:
            for i in range(len(self.my_particle.particles)):
                x_particle = self.my_particle.particles[i,0]
                y_particle = self.my_particle.particles[i,1]
                if self.check_obsb(x_particle, y_particle)==True:
                    self.my_particle.particles[i,0]=self.avg_x+uniform(-0.1,0.1)
                    self.my_particle.particles[i,1]=self.avg_y+uniform(-0.1,0.1)




            
        
    # def Update_FOV_Filter(self,x_center, y_center):
        #update particles in FOV
        #rearrange particles based on current FOV & static obstacles
        # Intelligent sampling
        
        # self.my_par
 

    def Update_Measurement_Filter(self,x_center, y_center):
        #Update the filter with the last measurements
        
        self.my_particle.update(x_center, y_center)
        # print self.my_particle.weights
        current_entropy=self.my_particle.get_entropy()
        weight_var=np.var(100*self.my_particle.weights, dtype=np.float64)
        # rospy.loginfo("variance of particles before: %.6lf, entropy: %.3lf",weight_var,current_entropy)
        self.my_particle.resample('residual')
        # self.my_particle.resample('residual')
        weight_var=np.var(100*self.my_particle.weights, dtype=np.float64)
        current_entropy=self.my_particle.get_entropy()
        # weight_var=np.var(self.my_particle.weights)
        # rospy.loginfo("variance of particles after: %.6lf, entropy: %.3lf",weight_var,current_entropy)
        


        self.context_particle.update(x_center, y_center)
        self.context_particle.resample('residual')

        # current_entropy=self.my_particle.get_entropy()
        # ParticlesContribution=self.my_particle.returnParticlesContribution()
        # rospy.loginfo("resampledparticle : "+str(current_entropy))
        # rospy.loginfo("contribution : "+str(ParticlesContribution))


    def PositionMeasurementCb(self,msg):
        #recieve poses array from measurement

        # rospy.loginfo("object detected")
        poses_array=msg.people
        detected_people=len(poses_array)
        if(detected_people==0):
            self.search_mode=1
            return
        else:
            # rospy.loginfo("object detected")
            self.search_mode=0
            self.context_activate=0
            self.counter=0
            detected_=True

        x_center=poses_array[0].pos.x
        y_center=poses_array[0].pos.y
        self.target_z=poses_array[0].pos.z
        # rospy.loginfo("x_center: %.2lf, y_center: %.2lf", x_center, y_center)
        # z_center=poses_array[0].position.z
        #data association

        #Let's say we are tracking som ar tags tracking chairs
        #update center of 3d objects center

        #add noises
        coin = np.random.uniform()
        if(coin >= 1.0-self.noise_probability): 
            x_noise = float(np.random.uniform(-0.15, 0.15))
            y_noise = float(np.random.uniform(-0.15, 0.15))
                # z_noise = int(np.random.uniform(-300, 300))
        else: 
            x_noise = 0
            y_noise = 0
                # z_noise = 0
        x_center += x_noise
        y_center += y_noise
        # z_center += z_noise
        
        self.Estimate_Filter()
        self.Update_Measurement_Filter(x_center,y_center)
        # rospy.loginfo("last statement in estimatefilter")
        self.last_callbacktime=rospy.get_time()

   

    def visualizeparticles(self):
        cloud_sets=[]
        for x_particle, y_particle in self.my_particle.particles.astype(float):
            cloud_sets.append([x_particle,y_particle,self.target_z])
        
        header=std_msgs.msg.Header()
        header.stamp=rospy.Time.now()
        header.frame_id='map'
        # header.frame_id='/world'
        particle_pcl=pcl2.create_cloud_xyz32(header,cloud_sets)
        # print particle_pcl.fields
        self.pcl_pub.publish(particle_pcl)
        # print "visualize detected"
        # rospy.loginfo("visualize particles")
        # rospy.loginfo("last statement in estimatefilter")

        cloud_sets2=[]
        for x_particle2, y_particle2 in self.context_particle.particles.astype(float):
            cloud_sets2.append([x_particle2,y_particle2,self.target_z])
            # rospy.loginfo("x : %.2lf, y : %.2lf ", x_particle2, y_particle2)
        
        # rospy.loginfo("length of context particle:%d ", len(cloud_sets2))
        header=std_msgs.msg.Header()
        header.stamp=rospy.Time.now()
        header.frame_id='map'
        # header.frame_id='/world'
        particle_pcl2=pcl2.create_cloud_xyz32(header,cloud_sets2)
        self.pcl_context_pub.publish(particle_pcl2)
        # print "visualize detected"
        # rospy.loginfo("visualize particles")
    
    def rejectparticles(self):
        cloud_sets3=[]
        avg_count=0
        # calculate the particle without FOV
        # obtain average locations of the paritlce that are not in FOV
        for x_particle, y_particle in self.my_particle.particles.astype(float):
            if self.Is_inFOV(x_particle,y_particle)==False:
                self.avg_x+=x_particle
                self.avg_y+=y_particle
                avg_count=avg_count+1;
                # rospy.loginfo("x: %.2lf, y: %.2lf", x_particle,y_particle)
                cloud_sets3.append([x_particle,y_particle,0.9])
                
        if avg_count<30:
            return
        else:
            # rospy.loginfo("avg count not in FOV:%d", avg_count )
            header=std_msgs.msg.Header()
            header.stamp=rospy.Time.now()
            header.frame_id='map'
            # header.frame_id='/world'
            particle_pcl3=pcl2.create_cloud_xyz32(header,cloud_sets3)
            self.pcl_fov_pub.publish(particle_pcl3)
        

        self.avg_x=self.avg_x/avg_count;
        self.avg_y=self.avg_y/avg_count;

        avg_pose=PoseStamped()
        avg_pose.pose=Pose(Point(self.avg_x,self.avg_y,self.target_z),Quaternion(0,0,0,1))
        avg_pose.header.stamp=rospy.Time.now()
        avg_pose.header.frame_id='map'
        self.avg_point_pub.publish(avg_pose)

        #update interior particles in my_particles
        for i in range(len(self.my_particle.particles)):
            x_particle = self.my_particle.particles[i,0]
            y_particle = self.my_particle.particles[i,1]
            if self.Is_inFOV(x_particle,y_particle)==True:
                self.my_particle.particles[i,0]=self.avg_x+uniform(-0.2,0.2)
                self.my_particle.particles[i,1]=self.avg_y+uniform(-0.2,0.2)


                
        # for x_particle, y_particle in self.my_particle.particles.astype(float):
            # if self.Is_inFOV(x_particle,y_particle)==True:
                # x_particle=0.0
                # y_particle=1.0
                # x_particle=self.avg_x+uniform(-0.2,0.2)
                # y_particle=self.avg_y+uniform(-0.2,0.2)

       
	

    def listener(self,wait=0.0):
        # rospy.spin()
        while not rospy.is_shutdown():
            self.rejectparticles()
            self.Estimate_Filter()
            # self.filter_by_Occgrids()
            self.visualizeparticles()
            cur_time=rospy.get_time()
            duration = cur_time -self.last_callbacktime
            # rospy.loginfo("duration : %lf", duration)
            if duration>6:
                self.search_mode=1
            else:
                self.search_mode=0
            
            # test_point_x=0.56
            # test_point_y=-0.56
            # res=self.Is_inFOV(test_point_x,test_point_y)
            # rospy.loginfo("result: %d",res )
            
            # rospy.spinOnce()
            rospy.Rate(2).sleep()


if __name__ == '__main__':
        rospy.init_node('Ar_particle_filter_test')
	# print("Initialize node")
        tracker_manager = ArTracker(sys.argv[1] if len(sys.argv) >1 else 0.0)
	tracker_manager.listener()	
        # while not rospy.is_shut_down():
            # ArTracker.visualizeparticles()
            # print "hello"
            # rospy.sleep(1.0)
        

