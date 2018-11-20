#!/usr/bin/python3

import os, uuid, rdflib, logging, shutil

class MockIPFS:
    def __init__(self, base_dir):
        self.base_dir = base_dir
        logging.debug("Mock IPFS running at %s" % self.base_dir)
        os.makedirs(self.base_dir, exist_ok=True)

    def add_bytes(self, blob):
        r = str(uuid.uuid4())
        logging.debug("Writing blob %s" % r)
        with open(os.path.join(self.base_dir, r), 'wb') as f:
            f.write(blob)
        return r

    def cat(self, r):
        logging.debug("Reading blob %s" % r)
        with open(os.path.join(self.base_dir, r[-36:]), 'rb') as f:
            return f.read()

    def get(self, r):
        try:
            shutil.copyfile(os.path.join(self.base_dir, r[-36:]), os.path.abspath(r[-36:]))
            logging.debug("Copying blob %s" % r)
        except shutil.SameFileError:
            logging.debug("Blob %s is already available" % r)
            pass
