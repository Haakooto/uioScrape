import argparse
import atexit
import hashlib
import os
import re
import subprocess
import sys
import time
from pathlib import Path

import numpy as np

from mounter import *


def get_hash_from_file(path):
    # returns a unique utf-8 encoded hash of pdf at given path
    m = hashlib.md5()
    try:
        with open(path, "rb") as file:
            
            m.update( file.read())
    except Exception as e:
        # if the above fails, create a hash from just the filename
        print(e)
        m.update(bytes(str(path).split("/")[-1].encode("utf-8")))
    return m.digest()


def generate_hash_file(dir, store=True):
    # generate a hash file from the pdfs within the given dir 
    globber = [str(file) for file in Path(dir).rglob('*.pdf')]
    hash_arr = np.array(["a"*16]*len(globber), dtype=bytes)
    for i,path in enumerate(globber):
        hash_arr[i] = get_hash_from_file(path)
    if store:
        np.save(dir+"/.hashes", hash_arr, allow_pickle=True, fix_imports=True)
    return hash_arr



filename_count_re = re.compile(r"([^(]+?)(\(\d+\))?\.pdf", re.IGNORECASE)
def download_subject(subject):
    # starts the downloading process. Also makes sure that no duplicate files are downloaded

    dl_dir = os.path.abspath("./downloads/"+subject) # dir to be downloaded into
    if not os.path.isdir(dl_dir): # if not dir exists, create it 
        subprocess.run(["mkdir", "-p", dl_dir])

    hash_arr = list(generate_hash_file(dl_dir, store=False)) # create an empty hash file and get an empty list of correct length
    mnt_pathglob = list(Path('./.mnt/').rglob('*.pdf'))
    dl_pathglob = list(Path(dl_dir).rglob('*.pdf'))
    dl_pathglob_names = [re.findall(filename_count_re, file.name)[0][0] for file in dl_pathglob] # get names without extension or count number (#)
    for file in mnt_pathglob:
        print_suffix = ""
        hashed_file = get_hash_from_file(file)
        
        filename = re.findall(filename_count_re, str(file.name))[0][0]# strip .pdf extension in order to compare count number
        if hashed_file not in hash_arr:
            # if this succeeds, then the pdf is unique and will be downloaded
            if "lect" in filename: continue
            if "not" in filename: continue
            if filename in dl_pathglob_names:
                occurances = sum([filename == foo for foo in dl_pathglob_names])
                filename = str(filename).rstrip(".pdf") 
                assert occurances > 0, "I mean, if this gets called, you really fucked up"
                if occurances > 0:
                    filename += f"_{occurances}"
                print_suffix = "(Added suffix due to existing filename)"
                
            dl_pathglob_names.append(filename)
            filename+=".pdf"
            
            subprocess.run(["cp","-p", file, f"{dl_dir}/{filename}"])
            print(f"Downloaded {filename}.pdf" + print_suffix)
            time.sleep(0.1)
            hash_arr.append(hashed_file)
        else:
            pass
            print(f"(Duplicate) Skipped {filename}.pdf")
    generate_hash_file(dl_dir)
   



def scraper(subject):
    #starts the scraper
    subject = subject.upper()

    # the line below is an abomination which I intend to fix "later"
    subjects_dict = eval(np.load(os.path.relpath("./src/subjects.npy"), allow_pickle=True).__repr__().lstrip("array(").rstrip(",\n      dtype=object)\n"))
    try:
        dav_url = "https://www-dav.uio.no/studier/emner"+subjects_dict[subject]
    except KeyError:
        print(f"Subject '{subject}' not found")
        sys.exit(1)

    init_mountcheck()
    atexit.register(unmount_webdav)
    mount_webdav(dav_url) 
    download_subject(subject)
    atexit.unregister(unmount_webdav)
    unmount_webdav()


parser = argparse.ArgumentParser(description='Scrape all semester pages of a UiO subject in order to get the urls of PDFs of old exams and their solutions.\n Made by Bror Hjemgaard, 2021')
parser.add_argument('SUBJECT', metavar='SUBJECT', nargs=1,
                    help='Subject code of any UiO subject. Case insensitive')
    

if __name__ == '__main__':
    args = parser.parse_args()
    subject = args.SUBJECT[0]

    scraper(subject)
    
