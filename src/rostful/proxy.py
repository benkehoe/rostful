from __future__ import absolute_import

import roslib
roslib.load_manifest('rostful')
import rospy

import urllib2, json, sys

import threading
from cStringIO import StringIO

from importlib import import_module

from . import message_conversion as msgconv
from . import deffile

from .util import ROS_MSG_MIMETYPE,ROS_MSG_MIMETYPE_WITH_TYPE, get_json_bool

class IndividualServiceProxy:
	def __init__(self,url,name,srv_module,service_type_name, binary=None):
		self.url = url
		self.name = name
		
		self.binary = binary or False
		
		self.rostype = getattr(srv_module,service_type_name)
		self.rostype_req = getattr(srv_module,service_type_name + 'Request')
		self.rostype_resp = getattr(srv_module,service_type_name + 'Response')
		
		self.proxy = rospy.Service(self.name,self.rostype,self.call)
	
	def call(self,rosreq):
		if self.binary:
			req = StringIO()
			rosreq.serialize(req)
			reqs = req.getvalue()
			content_type = ROS_MSG_MIMETYPE_WITH_TYPE(self.rostype_req)
		else:
			req = msgconv.extract_values(rosreq)
			req['_format'] = 'ros'
			reqs = json.dumps(req)
			content_type = 'application/json'
		
		wsreq = urllib2.Request(self.url,data=reqs,headers = {'Content-Type': content_type})
		try:
			wsres = urllib2.urlopen(wsreq)
		except Exception, e:
			raise e
		
		if wsres.getcode() != 200:
			#TODO: flip out
			pass
		
		data_str = wsres.read().strip()
		
		rosresp = self.rostype_resp()
		if wsres.info()['Content-Type'].split(';')[0].strip() == ROS_MSG_MIMETYPE:
			rosresp.deserialize(data_str)
		else:
			data = json.loads(data_str)
			data.pop('_format',None)
			msgconv.populate_instance(data,rosresp)
		
		return rosresp

def create_service_proxy(url,name,service_type,binary=None):
	try:
		service_type_module,service_type_name = tuple(service_type.split('/'))
		roslib.load_manifest(service_type_module)
		srv_module = import_module(service_type_module + '.srv')
		return IndividualServiceProxy(url, name, srv_module, service_type_name, binary=binary)
	except Exception, e:
		print "Unknown service type %s" % service_type
		return None
	

class IndividualTopicProxy:
	def __init__(self,url,name,msg_module,topic_type_name,pub=True,sub=True, publish_interval=None, binary=None):
		self.url = url
		self.name = name
		
		self.binary = binary or False
		
		self.pub = pub
		self.sub = sub
		
		self.rostype = getattr(msg_module,topic_type_name)
		
		self.publish_interval = publish_interval or 1
		self.publisher = None
		if self.pub:
			self.publisher = rospy.Publisher(name, self.rostype)
			self.publisher_thread = threading.Thread(target=self.publish,name=name)
			self.publisher_thread.start()
		
		self.subscriber = None
		if self.sub:
			self.subscriber = rospy.Subscriber(name, self.rostype, self.callback)
	
	def callback(self,msg):
		if self.binary:
			req = StringIO()
			msg.serialize(req)
			reqs = req.getvalue()
			content_type = ROS_MSG_MIMETYPE_WITH_TYPE(self.rostype_req)
		else:
			req = msgconv.extract_values(msg)
			req['_format'] = 'ros'
			reqs = json.dumps(req)
			content_type = 'application/json'
		
		wsreq = urllib2.Request(self.url,data=reqs,headers = {'Content-Type': content_type})
		try:
			wsres = urllib2.urlopen(wsreq)
		except Exception, e:
			raise e
		
		if wsres.getcode() != 200:
			#TODO: flip out
			pass
	
	def publish(self):
		stop = False
		while not (stop or rospy.is_shutdown()):
			req = {}
			req['_format'] = 'ros'
			reqs = json.dumps(req)
			
			if self.binary:
				content_accept = ROS_MSG_MIMETYPE
			else:
				content_accept = 'application/json'
			
			wsreq = urllib2.Request(self.url,headers = {'Accept': content_accept})
			try:
				wsres = urllib2.urlopen(wsreq)
			except Exception, e:
				sys.stderr.write("Encountered an error while retrieving a message on topic %s: %s\n" % (self.name, str(e)))
				stop = True
				break
			
			if wsres.getcode() != 200:
				#TODO: flip out
				pass
			
			data_str = wsres.read().strip()
			
			msg = self.rostype()
			if wsres.info()['Content-Type'].split(';')[0].strip() == ROS_MSG_MIMETYPE:
				msg.deserialize(data_str)
			else:
				data = json.loads(data_str)
				data.pop('_format',None)
				msgconv.populate_instance(data,msg)
			
			self.publisher.publish(msg)
			rospy.sleep(self.publish_interval)

def create_topic_proxy(url,name,topic_type,pub=True,sub=True, publish_interval=None, binary=None):
	try:
		topic_type_module,topic_type_name = tuple(topic_type.split('/'))
		roslib.load_manifest(topic_type_module)
		msg_module = import_module(topic_type_module + '.msg')
		return IndividualTopicProxy(url, name, msg_module, topic_type_name, 
								pub=pub, sub=sub, publish_interval=publish_interval, binary=binary)
	except Exception, e:
		print "Unknown msg type %s" % topic_type
		return None

class RostfulServiceProxy:
	def __init__(self,url,remap=False, subscribe=False, publish_interval=None, binary=None, prefix=None):
		if url.endswith('/'):
			url = url[:-1]
		self.url = url
		
		self.binary = binary
		
		self.prefix=prefix
		
		self.services = {}
		self.topics = {}
		
		self.subscribe = subscribe
		self.publish_interval = publish_interval
		
		config_url = self.url + '/_rosdef'
		
		req = urllib2.Request(config_url)
		
		try:
			res = urllib2.urlopen(req)
		except Exception, e:
			#TODO: flip out
			raise e
		
		if res.getcode() != 200:
			#TODO: flip out
			pass
		
		parser = deffile.DefFileParser()
		parser.add_default_section_parser(deffile.INISectionParser)
		
		dfile = parser.parse(res.read().strip())
		
		if dfile.type == 'Node':
			if self.prefix is None:
				prefix = dfile.manifest['Name'] or ''
			else:
				prefix = self.prefix
			if prefix:
				prefix += '/'
			services = dfile.get_section('Services')
			if services:
				print 'Services:'
				for service_name, service_type in services.fields.iteritems():
					ret = self.setup_service(self.url + '/' + service_name, prefix + service_name, service_type, remap=remap)
					if ret: print '%s (%s)' % (prefix + service_name, service_type)
			
			topics = dfile.get_section('Topics')
			if topics:
				print 'Publishing and subscribing:'
				for topic_name, topic_type in topics.fields.iteritems():
					ret = self.setup_topic(self.url + '/' + topic_name, prefix + topic_name, topic_type, pub=True, sub=subscribe, remap=remap, publish_interval=publish_interval)
					if ret: print '%s (%s)' % (prefix + topic_name, topic_type)
			
			published_topics = dfile.get_section('Publishes')
			if published_topics:
				print 'Publishing:'
				for topic_name, topic_type in published_topics.fields.iteritems():
					ret = self.setup_topic(self.url + '/' + topic_name, prefix + topic_name, topic_type, pub=True, remap=remap, publish_interval=publish_interval)
					if ret: print '%s (%s)' % (prefix + topic_name, topic_type)
			
			if subscribe:
				subscribed_topics = dfile.get_section('Subscribes')
				if subscribed_topics:
					print 'Subscribing:'
					for topic_name, topic_type in subscribed_topics.fields.iteritems():
						ret = self.setup_topic(self.url + '/' + topic_name, prefix + topic_name, topic_type, pub=False, sub=subscribe, remap=remap, publish_interval=publish_interval)
						if ret: print '%s (%s)' % (prefix + topic_name, topic_type)
		elif dfile.type == 'Service':
			self.setup_service(self.url, dfile.manifest['Name'], dfile.manifest['Type'], remap=remap)
		elif dfile.type == 'Topic':
			pub = dfile.manifest['Subscribes'].lower() == 'true'
			sub = dfile.manifest['Publishes'].lower() == 'true' and subscribe
			self.setup_service(self.url, dfile.manifest['Name'], dfile.manifest['Type'], pub=pub, sub=sub, remap=remap, publish_interval=publish_interval)
		return
	
	def setup_service(self, service_url, service_name, service_type, remap=False):
		if remap:
			service_name = service_name + '_ws'
		
		proxy = create_service_proxy(service_url,service_name,service_type, binary=self.binary)
		if proxy is None: return False
		self.services[service_name] = proxy
		return True
	
	def setup_topic(self, topic_url, topic_name, topic_type, pub=None, sub=None, remap=False, publish_interval=None):
		if remap:
			topic_name = topic_name + '_ws'
		
		if pub is None and sub is None:
			pub = True
			sub = True
		
		proxy = create_topic_proxy(topic_url,topic_name,topic_type,pub=pub,sub=sub, publish_interval=publish_interval, binary=self.binary)
		if proxy is None: return False
		self.topics[topic_name] = proxy
		return True

import argparse

def proxymain():
	rospy.init_node('wsproxy',anonymous=True)
	
	parser = argparse.ArgumentParser()
	
	parser.add_argument('url')
	
	parser.add_argument('--publish-interval','--pub-interval',type=float)
	
	parser.add_argument('--allow-subscription','--sub',dest='subscribe',action='store_true',default=False)
	
	parser.add_argument('--binary',action='store_true',default=False)
	
	parser.add_argument('--test',action='store_true',default=False)
	
	grp = parser.add_mutually_exclusive_group()
	grp.add_argument('--prefix')
	grp.add_argument('--no-prefix', action='store_const', const = '', dest='prefix')
	
	args = parser.parse_args(rospy.myargv()[1:])
	
	if not args.url.startswith('http'):
		args.url = 'http://' + args.url
	
	proxy = RostfulServiceProxy(args.url, remap=args.test, subscribe=args.subscribe, publish_interval = args.publish_interval, binary=args.binary, prefix=args.prefix)
	
	rospy.spin()