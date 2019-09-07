#!/usr/bin/python3

import os, io, uuid, rdflib, logging, posixpath, subprocess, time

DELAY=0

class MockIPFSObject:
    def put(self, ipld_blob):
        logging.info("MOCKIPFS: object_put")
        time.sleep(DELAY)
        i = ipld_blob.read()
        logging.debug(i)
        if "FALSE_OLD_IPFS" in os.environ:
            cp = subprocess.run(['ipfs', 'object', 'put', '-'], input=i, stdout=subprocess.PIPE)
        else:
            cp = subprocess.run(['ipfs', 'object', 'put', '-q', '-'], input=i, stdout=subprocess.PIPE)
        if cp.returncode != 0:
            raise OSError("Error running ipfs object put: %s" % cp.stderr)
        hash = cp.stdout.decode('us-ascii').strip()
        if hash.startswith("added "):
            hash = hash[6:]
        return {"Hash": hash}


class MockIPFS:
    def __init__(self, base_dir):
        self.base_dir = base_dir
        self.object = MockIPFSObject()
        logging.debug("Mock IPFS running at %s" % self.base_dir)
        os.makedirs(self.base_dir, exist_ok=True)

    def add_bytes(self, blob):
        logging.info("MOCKIPFS: add_bytes")
        time.sleep(DELAY)
        cp = subprocess.run(['ipfs', 'add', '-Q', '-'], input=blob, stdout=subprocess.PIPE)
        if cp.returncode != 0:
            raise OSError("Error running ipfs add: %s" % cp.stderr)
        return cp.stdout.decode('us-ascii').strip()

    def cat(self, nuri):
        logging.info("MOCKIPFS: cat %s" % nuri)
        time.sleep(DELAY)
        cp = subprocess.run(['ipfs', 'cat', nuri], stdout=subprocess.PIPE)
        if cp.returncode != 0:
            raise OSError("Error running ipfs cat: %s" % cp.stderr)
        return cp.stdout

    def get(self, nuri):
        logging.info("MOCKIPFS: get %s" % nuri)
        time.sleep(DELAY)
        if not nuri.startswith("/ipfs/"):
            raise IOError("%s: not a NURI, can't save" % nuri)
        # assume appropriate directories have been created
        with open(posixpath.basename(nuri), 'wb') as f:
            cp = subprocess.run(['ipfs', 'cat', nuri], stdout=f)

        if cp.returncode != 0:
            raise OSError("Error running ipfs cat: %s" % cp.stderr)
