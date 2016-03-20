#!/usr/bin/python
import sys
import os
import json
import subprocess
import time

def init(directory):
	config = {"versions":[], "directory":directory}
	with open("ipfs-publish.json", 'w') as f:
		f.write(json.dumps(config, sort_keys=True, indent=4, separators=(',', ': ')))

def new(hash_str):
	return {"date_time": time.strftime("%Y-%m-%d %H:%M:%S"),
			"hash": hash_str}

def publish():
	config = {}
	with open("ipfs-publish.json", 'r') as f:
		config = json.loads(f.read())
	
	if os.path.isdir(config["directory"]):
		print "Publishing", config["directory"]
		proc = subprocess.Popen("ipfs add -q -r " + config["directory"], stdout=subprocess.PIPE, shell=True)
		(out, err) = proc.communicate()

		hs = out.split('\n')
		hash_str = hs[-2]

		if err != None:
			print err
			exit(1)
		elif len(config["versions"]) > 0 and hash_str == config["versions"][-1]["hash"]:
			print "Nothing to update."
			exit(1)
		else:
			print hash_str
			config["versions"].append(new(hash_str))

			with open("ipfs-publish.json", 'w') as f:
				f.write(json.dumps(config, sort_keys=True, indent=4, separators=(',', ': ')))

			return hash_str

def deploy():
	config = {}
	with open("ipfs-publish.json", 'r') as f:
		config = json.loads(f.read())

	if len(config["versions"]) > 0:
		hash_str = config["versions"][-1]["hash"]

		proc = subprocess.Popen(
			"ssh -t basile@basilehenry.com \"ipfs pin add " +
			hash_str +
			" && ipfs name publish " +
			hash_str +
			"\"", stdin=subprocess.PIPE, stdout=subprocess.PIPE, shell=True)
		(out, err) = proc.communicate()
		print err if err != None else out

def main():

	if len(sys.argv) < 2:
		print "Usage: ./ipfs-publish.py [init <directory> | publish | deploy]"
	elif sys.argv[1] == "init" and len(sys.argv) > 2:
		if os.path.isdir(sys.argv[2]):
			init(sys.argv[2])
			exit(0)
		else:
			print sys.argv[2], "is not a directory."
	elif sys.argv[1] == "publish":
		if os.path.isfile("ipfs-publish.json"):
			publish()
			exit(0)
		else:
			print "You have to \"./ipfs-publish.py init <directory>\" before you can publish."
	elif sys.argv[1] == "deploy":
		if os.path.isfile("ipfs-publish.json"):
			deploy()
			exit(0)
	else:
		print "Usage: ./ipfs-publish.py [init <directory> | publish | deploy]"

	exit(1)

if __name__ == "__main__": main()