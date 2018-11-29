#!/usr/bin/python3

import os, io, uuid, rdflib, logging, posixpath, subprocess

class MockIPFS:
    def __init__(self, base_dir):
        self.base_dir = base_dir
        logging.debug("Mock IPFS running at %s" % self.base_dir)
        os.makedirs(self.base_dir, exist_ok=True)

    def add_bytes(self, blob):
        cp = subprocess.run(['ipfs', 'add', '-Q', '-'], input=blob, capture_output=True)
        if cp.returncode != 0:
            raise OSError("Error running ipfs add: %s" % cp.stderr)
        return cp.stdout.decode('us-ascii').strip()

    def object_put(self, ipld_blob):
        i = ipld_blob.read()
        logging.debug(i)
        cp = subprocess.run(['ipfs', 'object', 'put', '-q', '-'], input=i, capture_output=True)
        if cp.returncode != 0:
            raise OSError("Error running ipfs object put: %s" % cp.stderr)
        return {"Hash": cp.stdout.decode('us-ascii').strip()}

    def cat(self, nuri):
        cp = subprocess.run(['ipfs', 'cat', nuri], capture_output=True)
        if cp.returncode != 0:
            raise OSError("Error running ipfs cat: %s" % cp.stderr)
        return cp.stdout

    def get(self, nuri):
        if not nuri.startswith("/ipfs/"):
            raise IOError("%s: not a NURI, can't save" % nuri)
        # assume appropriate directories have been created
        with open(posixpath.basename(nuri), 'wb') as f:
            cp = subprocess.run(['ipfs', 'cat', nuri], stdout=f)

        if cp.returncode != 0:
            raise OSError("Error running ipfs cat: %s" % cp.stderr)
