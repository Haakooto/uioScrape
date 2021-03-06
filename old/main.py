from html.parser import HTMLParser
import http.client
import re, sys, requests, threading, itertools, time, argparse, os
from urllib import request
import urllib.error
from downloading import download_pdf
import queue, _queue
import numpy as np

semester_exam_index_remover_regex = re.compile(r"(?s)(?:.*?)/[vh]\d{2}/eksamen/", re.IGNORECASE)

global ignore_urls, disqualifiers, num_requests, priorities # most of these can be removed, i just cant be bothered rn
#todo: add more elegant way of importing these values, through np.loadtxt
with open(os.path.relpath("src/ignores.txt"), "r") as file:
    ignore_urls = [foo.rstrip() for foo in file.readlines()[1:]]
with open(os.path.relpath("src/disqualifiers.txt"), "r") as file:
    disqualifiers = [foo.rstrip() for foo in file.readlines()[1:]]
priorities = {}
with open(os.path.relpath("src/priorities.txt"), "r") as file:
    for line in sorted(file.readlines(), key=len):
        words = line.rstrip().split()
        priorities[words[0]] = int(words[-1])

# list of strings which will automatically disqualify a pdf from being stored
#ignore_pdfs = ["devilry","lecture", "smittevern", "Lecture", "oblig", "week", "Week", "exercise", "Oblig", "ukesoppgave", "Ukesoppgave", "oppgave", "Oppgave"]
ignore_pdfs = ["currently disabled"]
num_requests = 0

class Url(str):
    def __new__(cls, *args, **kw):
        return str.__new__(cls, *args)

    def __init__(self,url,  parent=None): 
        self.provided_url = url
        self.url = url
        self.parent = parent
       
        if not "www." not in self.url and "http" not in self.url:
            assert isinstance(self.parent, Url), f"Error, local url {self.provided_url} provided without parent!"
            self.url = self.merge(self.parent)

        else:
            self.url = self.url.replace("https://www.","").replace("http://www.","").split("#")[0].split("?")[0] # remove query

    def __str__(self):
        return "https://www."+self.url
    
    def __repr__(self):
        return "https://www."+self.url
    
    def __class__(self):
        return str
    

    def __hash__(self):
        return hash(self.url)

    def merge(self, master):
        rel = self.url
        master = master.url
        # merges a relative url with the parent, creating an absolute path
        rel= rel.lstrip("/")
        if master == "":
            return rel
        if master.startswith("http"):
            master = master.lstrip("https://").rstrip("/")


        if rel.startswith("http"):
            rel = rel.lstrip("https://")
        master = master.split("/")
        rel = rel.split("/")
        for idx,sub in enumerate(master):
            if sub == rel[0]:
                return "https://"+"/".join(master[:idx] + rel )
        res = "https://"+"/".join(master + rel )
        if " " in res:
            res = res[res.index(" "):]
        return res



class LinkScrape:
    urls = []
    parent_urls = []
    visited = []
    pdfs = {}
    valid_pdfs = {}

    subject_subfaculty_dict = {"fys":"fys", "fys-mek":"fys", "in":"ifi", "mat":"math", "mek":"math", "ast":"astro", "kjm":"kjemi", "bios":"ibv", "bios-in":"ibv", "farm":"farmasi", "fys-stk":"fys", "mat-inf":"math", "fys-mena":"fys", "geo-ast":"geofag", "geo-deep":"geofag", "geo":"geofag"}

    def __init__(self, subject,  max_requests, speed, quality_check, tolerance):
        self.max_requests = max_requests
        self.requests_done = 0
        self.speed = speed
        self.quality_check = quality_check
        self.tolerance = tolerance
        

        self.base_url = "https://www.uio.no/studier/emner/matnat/"
        self.subject_code = subject.upper()
        subject_regex = re.search(r"([a-zA-Z\-]+)(\d+)", self.subject_code)
        if subject_regex:
            self.subject_type = subject_regex.group(1)
            self.subject_uuid = subject_regex.group(2)
        else:
            print(f"Error, subject code '{subject}' is note valid")
            sys.exit(1)
        try:
            self.subfaculty = self.subject_subfaculty_dict[self.subject_type.lower()]
        except KeyError:
            print(f"Subject '{subject}' of type '{self.subject_type}' not yet supported")
            sys.exit(1)
        
    
    def start(self, **kwargs):
        print(f"Starting scrape of {self.subject_code} of max requests {self.max_requests}")
        self.start_index_scraper()

        self.urls_to_be_checked = self.parent_urls.copy()
          
        try:
            for i in range(10): # 10 iterations should be mooore than enough
                parallel_result = self.fetch_parallel(self.urls_to_be_checked)
                url_parents = [foo[0] for foo in parallel_result]
                url_childs = [[Url(bar,parent=parent) for bar in foo[1:]] for foo,parent in zip(parallel_result, url_parents)]
                unique_urls = list(frozenset().union(*[set(foo) for foo in url_childs]))
                new_urls_to_be_checked = []
                for url in unique_urls:
                    if scrape_result:=self.check_url_and_update_storage(url):
                            new_urls_to_be_checked.append(scrape_result)

                if self.requests_done >= self.max_requests:
                    break
                self.urls_to_be_checked = reorder_urls_by_priority(new_urls_to_be_checked.copy(), self.tolerance)
        
        except KeyboardInterrupt:
            pass
        if self.quality_check:
            self.purge_404()
        else:
            self.valid_pdfs = self.pdfs
        
        self.valid_pdfs = {k: v for k, v in sorted(self.valid_pdfs.items(), reverse=True, key=lambda item: item[1])}

        
    def purge_404(self):
        print(f"\nFound {len(self.pdfs.keys())} potential pdfs.")
        sys.stdout.write(f"Quality check: 0% \r" )
        sys.stdout.flush()
        def check_status(pdf,name):
            if requests.get(pdf).status_code != 404:
                self.valid_pdfs[name] = pdf
            

        def divide_chunks(l, n): 
            for i in range(0, len(l), n):  
                yield l[i:i + n] 
        
      
        
        #q = queue.Queue()
        #res = []
        heap_size = 10 # only create thread heaps of sizes 10 or less
       
        unchecked_pdfs  = list(divide_chunks(list(self.pdfs.values()), heap_size)) #split into segments of length 10 for threading
        unchecked_pdf_names = list(divide_chunks(list(self.pdfs.keys()), heap_size))  # --""--

        for i,(pdfs, names) in enumerate(zip(unchecked_pdfs, unchecked_pdf_names)):
            threads = [threading.Thread(target=check_status, args = (pdf,name)) for pdf,name in zip(pdfs, names)]
            for t in threads:
                t.start()
                time.sleep(self.speed)
            for t in threads:   
                t.join()
            sys.stdout.write(f"Quality check: {int((i+1)/len(unchecked_pdfs)*100)}% \r" )
            sys.stdout.flush()
        print(f"Quality check purged {len(self.pdfs.keys())-len(self.valid_pdfs.keys())} PDFs")
                
        

    def start_index_scraper(self):

        url = Url(f"https://www.uio.no/studier/emner/matnat/{self.subfaculty}/{self.subject_code}/")
        self.parent_urls.append(url)
        self.visited.append(url)
        data = request.urlopen(url).read().decode("latin-1")
        raw_parent_urls = extract_course_index(data, url)
        self.parent_urls = []
        
        for link in raw_parent_urls:
            if "@" in link: continue
            if link == url: continue
            if not link.startswith(url): continue
            if link.endswith("/index-eng.html"): continue
            

            if re.search(r".*\?[^/]+",link): continue
            if link.endswith("/index.html"):
               link = link[:-10]
               #print(link)
            if link.find('http') >= 0:
                if link not in self.urls:
                    self.visited.append(link)
                    self.parent_urls.append(link)
    

    def check_url_and_update_storage(self, url):
        """
        performs several checks on a given url and does one of three things:
        - discards the url if it is not of interest 
        - if url is of interest to dig deeper, it is stored
        - if path points to a pdf, it is stored as a pdf, but not as a potential url to dig deeper in
        in either case, the url is stored to it kan be skipped during future evaluation

        the passed url can be full or relative path
        """
        if url in self.visited: return 
        if url.endswith(".pdf"): 
            # storing and categorizing pdfs
            # to add: check if link yields a valid .pdf with 2xx response.
            
            if regex_res := re.search(r"\/(?:\W+\/)*([^\/]+\.pdf)", url):
                pdfname = regex_res.group(0)[1:]
                for ignore in ignore_pdfs:
                    if ignore in pdfname: return
                try:
                    if pdfname not in list(self.pdfs.keys()):
                        self.pdfs[pdfname] = url
                   
                except KeyError: return
            return
        
        if url.endswith(".tex"): return # ignore .tex files for now
        if url.find('http') >= 0:
            # storing and categorizing urls
            if url in self.visited: return
            if url in ignore_urls: return
            if re.search(r".*\?[^/]+",url): return # any urls with arguments passed after a ? in the url has proven uninteresting so far, so they are ignored
            if not "uio" in url: return
            if "@" in url: return
            if url in self.parent_urls: return
            if semester_exam_index_remover_regex.match(url): return
            if ".." in url: return
            if self.subject_type not in url: return
            for disc in disqualifiers:
                if disc in url: return
            if url not in self.urls:
                self.urls.append(url)
                return url


    def read_url(self,url, q):
        if self.requests_done < self.max_requests:
            self.requests_done += 1
            try:
                data =  request.urlopen(url).read()
                data = [url]+extract(data, url)
                q.put(data)
                sys.stdout.write("Requests completed: %i/%i \r" %(self.requests_done, self.max_requests))
                sys.stdout.flush()
            except urllib.error.HTTPError:
                print(f"Timed out: {url}")
            except urllib.error.URLError:
                print(f"No response: {url}")
            except http.client.InvalidURL:
                print(f"ERROR: Invalid url: {url}")
        else:
            print("Fatal error! Max requests limit breached! Exiting!")
            q.all_tasks_done()
            sys.exit(1)
    
        
    def fetch_parallel(self, urls_to_load):
        q = queue.Queue()
        res = []
        if len(urls_to_load) > self.max_requests- self.requests_done:
            url_range = self.max_requests- self.requests_done
        else:
            url_range = len(urls_to_load)
        threads = [threading.Thread(target=self.read_url, args = (url, q)) for url in urls_to_load[:url_range]]
        for t in threads:
            t.start()
            time.sleep(self.speed)
        for t in threads:
            try:
                data = q.get(block=True, timeout=0.1)
                res.append(data) 
            except _queue.Empty:
                pass
        for t in threads:   
            t.join()
            
        return res


def reorder_urls_by_priority(urls, tolerance=100):
    # tolerance is how many percent of the urls are to be returned
    global priorities

    pri_vals = np.zeros(len(urls))
    for i,url in enumerate(urls):
        for pri in priorities.keys():
            if pri in url.lower():
                pri_vals[i] = priorities[pri]
    pri_vals_args = np.argsort(pri_vals)[::-1]
    res = []
    for i,pri in enumerate(pri_vals_args):
        res.append(urls[pri])
    return res[:int(len(urls)*tolerance/100)]


def relative_to_absolute_url(link, parent_url):
    if link.startswith('http'):
        if not parent_url.startswith(link):
            return link
        return link
    
    # add check for correct top level domain
    if link.startswith("/"):
        link = link[1:]
    
    if link.startswith("studier/"):
        return "https://www.uio.no/" + link
    else:
        if parent_url.endswith("/"):
            return parent_url+link
        else:
            return parent_url +"/" + link




HTML_MAIN_LEFT_BODY_REGEX = re.compile(r"(?s)(<li class=\"vrtx-child\"><a class=\"vrtx-marked\"(?:.*?)</li>)", re.IGNORECASE)
course_index_left_menu_regex = re.compile(r"(?s)<a class=\"vrtx-marked\"(?:.*?)</a>(?:.*?)<ul>(.*?)</ul>", re.IGNORECASE)
course_left_menu_regex = re.compile(r"<a href=\"([^\"^#^@^\?]*)\"[^>]*>")
course_main_body_regex = re.compile(r"(?s)<!--startindex-->(.*)<!--stopindex-->", re.IGNORECASE)
course_messages_regex = re.compile(r"(?s)(<div class=\"vrtx-messages-header\">(?:.*?)</div>)(.*?)(<div class=\"vrtx-messages\">(?:.*?)</div>)", re.IGNORECASE)
course_index_semester_list_regex = re.compile(r"(?s)(<div class=\"vrtx-frontpage-box grey-box\" id=\"vrtx-course-semesters\">(?:.*?)</div>)", re.IGNORECASE)

extract_href_regex = re.compile(r'(?s)href=\"([^\"^#^@^\?\~]*)\"[^>]*>(?:.*?)</a>', re.IGNORECASE)

def extract_course_index(content, parent):
    if isinstance(content, tuple):
        content = content[0]
    if isinstance(content, bytes):
        content = content.decode("latin-1")
    course_semesters_list = course_index_semester_list_regex.findall(content)[0]
    course_semesters_urls = extract_href_regex.findall(course_semesters_list)
    course_semesters_urls = [Url(foo,parent=parent) for foo in course_semesters_urls]

    #extract content from left menu. try/except because will fail if nothing is in left menu otherwise
    try:
        course_left_menu = course_index_left_menu_regex.findall(content)
        course_left_menu_urls = extract_href_regex.findall(course_left_menu[0])
        course_left_menu_urls = [Url(foo, parent=parent) for foo in course_left_menu_urls]
        
        return course_semesters_urls + course_left_menu_urls
    except Exception as e:
        return course_semesters_urls
    


def purge_unwanted_urls(urls):
    accepted = []
    for url in urls:
        #print(url.lstrip("https://www.").rstrip("/") in ignore_urls, url.lstrip("https://www.").rstrip("/"))
        
        #url = url.replace("http:", "https:")
        if not url.lstrip("https://www.").rstrip("/") in ignore_urls:
            
            accepted.append(url)
        
    return accepted


def extract(content, parent_url):
    if isinstance(content, tuple):
        content = content[0]
    if isinstance(content, bytes):
        content = content.decode("latin-1")
    urls = extract_href_regex.findall(content)
    urls = purge_unwanted_urls(urls)
    for i,url in enumerate(urls):
        if not url.lstrip().startswith("http"):
            urls[i] = merge(parent_url, url)
    urls_ = []
    for url in urls:
        if "uio" in url and url not in urls_:
            urls_.append(url)
 
    return urls_

    


def merge(master, rel):
    # merges a relative url with the parent, creating an absolute path
    rel= rel.lstrip("/")
    if master == "":
        return rel
    if master.startswith("http"):
        master = master.lstrip("https://").rstrip("/")


    if rel.startswith("http"):
        rel = rel.lstrip("https://")
    master = master.split("/")
    rel = rel.split("/")
    for idx,sub in enumerate(master):
        if sub == rel[0]:
            return "https://"+"/".join(master[:idx] + rel )
    res = "https://"+"/".join(master + rel )
    if " " in res:
        res = res[res.index(" "):]
    return res
   







parser = argparse.ArgumentParser(description='Scrape all semester pages of a UiO subject in order to get the urls of PDFs of old exams and their solutions.\n Made by Bror Hjemgaard, 2021')
parser.add_argument('SUBJECT', metavar='SUBJECT', nargs=1,
                    help='Subject code of a matnat subject. Case insensitive')
parser.add_argument("-r", dest="requests",  metavar="requests", default=50, help="Maximum number of requests to make. Increase at own risk. Note that the actual number of requests will be higher than this if quality check is enabled. Default: 50")
parser.add_argument("-s", dest="speed",  metavar="speed", default=0.1, help="Sleep time (s) between requests. Decrease at own risk to make search faster. Default: 0.1")
parser.add_argument("-tol", dest="tolerance",  metavar="tolerance", default=80, help="Tolerance (%%) of how strict the sorting algorithm should be. Higher = less trict. Decrease when you get way to many un-intersting PDFs, and vice versa. Default: 80")

parser.add_argument('--Q', action="store_true",
                    help='Quality check. Excludes PDFs that return 404 responses. Will increase time and number of requests. Recommended if many of the returned PDFs return empty pages')
parser.add_argument('--d', action="store_true",
                    help='Enable downloading. If passed you will get the option to download the pdfs in bulk after the program is run')



if __name__ == '__main__':
    args = parser.parse_args()
    max_requests = int(args.requests)
    speed =float(args.speed)
    quality = bool(args.Q)
    download = bool(args.d)
    subject = args.SUBJECT[0]
    tolerance = int(args.tolerance)
    start = time.time()
    scraper = LinkScrape(subject = subject,  max_requests = max_requests, speed=speed, quality_check = quality, tolerance = tolerance)
    scraper.start()
    end = time.time()
    print()
    print(f"Found {len(scraper.valid_pdfs.keys())} items in {round(end-start,2)}s after {scraper.requests_done} requests")
    print("===RESULTS===")
    print("\n".join([f"#{i+1} {name}: {link}" for i, (name,link) in enumerate(scraper.valid_pdfs.items())]))
    print("=============")
    #print("\n".join([f"\u001b]8;;{link}\u001b\\{name}\u001b]8;;\u001b\\" for name,link in scraper.valid_pdfs.items()]))
    if download:
        print("Which PDFs do you want to download? (pass # sep. by space, or write 'all' to download all)")
        uin = input(">>")
        subject = scraper.subject_code
        if len(uin.split(" ")) == 1:
            if "all" in uin.lower():
                for name,url in scraper.valid_pdfs.items():
                    try:
                        download_pdf(url, name, subject)
                    except:
                        print(f"Unable to download \u001b]8;;{url}\u001b\\{name}\u001b]8;;\u001b\\")
            print("\nDownloading done!"+" "*20)
        else:
            for num in uin.split(" "):
                try: 
                    num = int(num)
                    assert 0<num <= len(scraper.valid_pdfs.keys())
                except:
                    print("error")
                name,url= list(scraper.valid_pdfs.items())[num-1]
                try:
                    download_pdf(url, name, subject)
                except:
                    print(f"Unable to download \u001b]8;;{url}\u001b\\{name}\u001b]8;;\u001b\\")
        
            print("\nDownloading done!" + " "*20)

