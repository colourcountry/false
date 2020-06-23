#!/usr/bin/python3

import logging, os, rdflib, re, subprocess, posixpath

F = rdflib.Namespace("http://id.colourcountry.net/false/")

def publish_media(g, output_dir):
    base = os.path.join(output_dir,"ipfs")
    os.makedirs(base,exist_ok=True)

    spo = g.triples((None, F.localPath, None))
    for s, p, o in spo:
        local_src = o

        if s.startswith("ipfs:/"):
            s=s[6:]
        elif s.startswith("/ipfs/"):
            s=s[6:]
        else:
            raise PublishError(f"Unrecognized IPFS (N)URI: {s}")

        local_dest = os.path.dirname(os.path.join(output_dir, "ipfs", *posixpath.split(s)))
        subprocess.run(["cp","-r",local_src,local_dest], capture_output=False)

    g.remove((None, F.localPath, None))
    return g
