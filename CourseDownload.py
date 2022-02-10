import requests
import json
import time
import os
import subprocess
import re
from hashlib import md5
from collections import namedtuple
import rsa
import base64
from tqdm import tqdm
import getpass

_baseUrl = "https://vod.cc.shu.edu.cn/"
_loginUrl = "app/oauth/2.0/login?login_type=outer"
_userInfo = "app/user/getUserInfo"
_oauthCallbackUrl = "app/oauth/2.0/authzCodeCallback"
_getCourseList = "app/system/course/subject/findSubjectVodList"
_getVideoList = "app/system/resource/vodVideo/getCourseListBySubject"
_getVideoInfo = "app/system/resource/vodVideo/getvideoinfos"
_playPage = "app/vodvideo/vodVideoPlay.d2j"

_keystr = '''-----BEGIN PUBLIC KEY-----
    MIGfMA0GCSqGSIb3DQEBAQUAA4GNADCBiQKBgQDl/aCgRl9f/4ON9MewoVnV58OLOU2ALBi2FKc5yIsfSpivKxe7A6FitJjHva3WpM7gvVOinMehp6if2UNIkbaN+plWf5IwqEVxsNZpeixc4GsbY9dXEk3WtRjwGSyDLySzEESH/kpJVoxO7ijRYqU+2oSRwTBNePOk1H+LRQokgQIDAQAB
    -----END PUBLIC KEY-----'''

_header = {'Accept': 'application/json, text/javascript, */*; q=0.01',
           'Content-type': 'application/x-www-form-urlencoded; charset=UTF-8'}
_oauth_uuid = "oauth_DQNiv=cNBzlCmk&oauth_RcL8d=PnEtVj2a"
_otherparam = "playTypeHls=true"

_pattern = "$subjName/$classDate/$lessonName/"

_fnClass = "课堂.mp4"
_fnScreen = "屏幕.mp4"
# eg. 形式与政策/2022-01-01/第1讲/课堂.mp4

CourseList = []  # list of Courseinfo
VideoList = []
OAuthKey = ""

Courseinfo = namedtuple("CourseInfo", ["subjectName", "teacherName", "subjectId", "classId"])
Videoinfo = namedtuple("VideoInfo", ["vid", "videoName", "url"])
Classinfo = namedtuple("ClassInfo",
                       ["subjectName", "sessionName", "teacherName", "id", "classDate", "video1", "video2"])


def encryptPass(passwd) -> str:
    pubkey = rsa.PublicKey.load_pkcs1_openssl_pem(_keystr.encode('utf-8'))
    encryptpwd = base64.b64encode(rsa.encrypt(passwd.encode('utf-8'), pubkey)).decode()
    return encryptpwd


def getOAuthKey(sess) -> str:
    r = sess.get(_baseUrl + _playPage)
    return base64.b64decode(re.search(r'(?<=vaule=").*(?=")', r.text).group(0)).decode()


def signRequest(vid: str, oauth_key: str):
    oauth_nonce = str(int(round(time.time() * 1000)))
    oauth_path = _baseUrl + _playPage + "?id=%s" % vid
    encoded_oauth_path = base64.b64encode(oauth_path.encode('utf-8')).decode()
    tmpstr = "/%s?id=%s&oauth-consumer-key=%s&oauth-nonce=%s&oauth-path=%s&%s&%s" % (
        _getVideoInfo, vid, oauth_key, oauth_nonce, encoded_oauth_path, _oauth_uuid, _otherparam)
    return oauth_nonce, encoded_oauth_path, md5(tmpstr.encode('utf-8')).hexdigest().lower()


def getUserInfo(sess: requests.Session):
    r = sess.post(_baseUrl + _userInfo, headers=_header)
    data = json.loads(r.text)
    if "error" in r.text:
        print(r.text)
        raise RuntimeError(2, "Login Error")
    return data


def getCourses(sess: requests.Session):
    global CourseList
    r = sess.post(_baseUrl + _getCourseList, data="pageIndex=1&pageSize=30&orderByType=", headers=_header)
    data = json.loads(r.text)
    if "error" in r.text:
        print(r.text)
        raise RuntimeError(2, "Error")
    for item in data["list"]:
        CourseList.append(
            Courseinfo(item["subjectName"], item["userList"][0]["userName"], item["subjectId"], item["teclId"]))


def getVideos(sess: requests.Session, subjid: str, classid: str):
    body = "orderField=courTimes&subjectId=%s&teclId=%s" % (subjid, classid)
    r = sess.post(_baseUrl + _getVideoList, body, headers=_header)
    if "error" in r.text:
        print(r.text)
        raise RuntimeError("Cannot get Video list")
    data = json.loads(r.text)
    resvlist = data["list"][0]["responseVoList"]
    lst = []
    for item in resvlist:
        lst.append(
            Classinfo(data["list"][0]["subjName"], item["videName"], item["userName"], item["id"], None, None, None))
    return lst


def getVideo(sess: requests.Session, vid: str, client_key: str):
    oauth_nonce, encpath, sign = signRequest(vid, client_key)
    h = _header
    h["oauth-consumer-key"] = client_key
    h["oauth-nonce"] = oauth_nonce
    h["oauth-path"] = encpath
    h["oauth-signature"] = sign
    body = "%s&id=%s&%s" % (_otherparam, vid, _oauth_uuid)
    r = sess.post(_baseUrl + _getVideoInfo, body, headers=h)
    if "error" in r.text:
        print(r.text)
        raise RuntimeError("Cannot get Url")
    data = json.loads(r.text)
    volist = data["videoPlayResponseVoList"]
    v1 = Videoinfo(volist[0]["id"], data["videName"], volist[0]["rtmpUrlHdv"])
    v2 = Videoinfo(volist[1]["id"], data["videName"], volist[1]["rtmpUrlHdv"])
    classbtime = time.strptime(data["videBeginTime"], "%Y-%m-%d %H:%M:%S")
    classetime = time.strptime(data["videEndTime"], "%Y-%m-%d %H:%M:%S")
    classdate = time.strftime("%Y-%m-%d", classbtime)
    return v1, v2, classdate


def download(url: str, fname: str, desc: str):
    resp = requests.get(url, stream=True)
    total = int(resp.headers.get('content-length', 0))
    with open(fname, 'wb') as file, tqdm(
            desc=desc,
            total=total,
            unit='B',
            unit_scale=True,
            unit_divisor=1024,
    ) as bar:
        for data in resp.iter_content(chunk_size=1024):
            size = file.write(data)
            bar.update(size)


def parseList(s: str, maxnum: int):
    noerror = True
    lst = s.replace(" ", "").split(",")
    finallist = []
    for item in lst:
        if not item.isnumeric():
            tmp = item.split("-")
            if len(tmp) == 2 and tmp[0].isnumeric() and tmp[1].isnumeric():
                if 0 < int(tmp[0]) <= int(tmp[1]) <= maxnum:
                    finallist.extend(range(int(tmp[0]), int(tmp[1]) + 1))
                else:
                    print("Invalid range: " + item)
                    noerror = False
            else:
                print("Error when parsing: " + item)
                noerror = False
        else:
            if 1 <= int(item) <= maxnum:
                finallist.append(int(item))
            else:
                print("Invalid index: " + item)
                noerror = False
    return sorted(list(set(finallist))), noerror


def login(username: str, encryptpwd: str) -> requests.Session:
    global OAuthKey
    print("Logging in...")
    session = requests.Session()
    try:
        r = session.get(_baseUrl + _loginUrl)
    except:
        print("\nUnable to connect:(\nPlease use VPN or check network settings")
        exit(1)
    if not r.url.startswith(
            ("https://oauth.shu.edu.cn/", "https://newsso.shu.edu.cn/")):
        # Already logined or error
        if not r.url.startswith(_baseUrl) or not r.status_code == 200:
            raise RuntimeError(2, "Unexpected Result")
    else:
        request_data = {"username": username, "password": encryptpwd}
        r = session.post(r.url, request_data)
        if not r.url.startswith(_baseUrl + _oauthCallbackUrl):
            if "too many requests" in r.text:
                raise RuntimeError(2, f"Too many Requests, try again later")
            raise RuntimeError(2, f"Login Failed")
        else:
            ssourl = re.search(r"(?<=script.src=').*(?=')", r.text).group(0)
            session.get(ssourl)
    userinfo = getUserInfo(session)
    username = userinfo['userCode']
    print("Login Successful:" + username)
    print("-------------------------")
    OAuthKey = getOAuthKey(session)
    return session


def getUrlsInList(lst: list, sess: requests.session) -> list:
    for idx, item in enumerate(lst):
        if item.classDate is None:
            v1, v2, cdate = getVideo(sess, item.id, OAuthKey)
            lst[idx] = Classinfo(item.subjectName, item.sessionName, item.teacherName, item.id, cdate, v1, v2)
    return lst


def main():
    username = input("User:")
    password = getpass.getpass("Password:")
    sess = login(username, encryptPass(password))
    getCourses(sess)
    print("Courses Available:")
    i = 0
    for course in CourseList:
        i += 1
        print("%d: %s by %s" % (i, course.subjectName, course.teacherName))
    print("\nYou may enter ranges. eg. 1,3-5,7")
    choice, noerror = parseList(input("Select Course(s) to download: "), len(CourseList))
    while not len(choice) >= 1 or not noerror:
        print("Invalid Input. Enter again.")
        choice, noerror = parseList(input("Select Course(s) to download: "), len(CourseList))
    print("You chose: ", end="")
    print(choice)
    allvideos = []  # list of List[ClassInfo]
    print("-" * 50)
    for index in choice:
        course: Courseinfo = CourseList[index - 1]
        allvideos.append(getVideos(sess, course.subjectId, course.classId))
    for j in range(0, len(choice)):
        course: Courseinfo = CourseList[choice[j] - 1]
        print("%d→ %s %s: %d videos" % (j + 1, course.subjectName, course.teacherName, len(allvideos[j])))
    print("""
    Menu\n
    1. Select video(s) to download\n
    2. Use IDM to download video(s)\n
    3. Get video info and URL(s)\n
    4. Get plain URL(s)\n
    5. Save to Markdown File
    """)
    while True:
        menuc = input("Enter[1-5]:")
        if menuc.isnumeric():
            if 1 <= int(menuc) <= 5:
                break
        print("Invalid Input")
    print("Please select ranges... eg. 1,3-5,7")
    dllist = []
    for j in range(0, len(choice)):
        course: Courseinfo = CourseList[choice[j] - 1]
        maxindex = len(allvideos[j])
        videoc, noerror = parseList(input(course.subjectName + " [1-%d]: " % maxindex), maxindex)
        while not len(choice) >= 1 or not noerror:
            print("Invalid Input. Enter again.")
            videoc, noerror = parseList(input(course.subjectName + " [1-%d]: " % maxindex), maxindex)
        print("You chose: ", end="")
        print(videoc)
        for index in videoc:
            dllist.append(allvideos[j][index - 1])
    print("Total %d videos" % len(dllist))
    tmp = input("Are you sure[Y/N]:")
    while True:
        if tmp == "Y" or tmp == "y":
            break
        else:
            if tmp == "N" or tmp == "n":
                print("Abort.")
                exit(0)
                break
            else:
                tmp = input("Please enter ""Y"" or ""N"" :")
    print("Please Wait while we get the links...")
    getUrlsInList(dllist, sess)
    if int(menuc) == 1:
        if os.name == 'nt':
            localpath = "D:\\downloads\\"
        else:
            localpath = "~/downloads/"
        print("Save to %s ?" % localpath)
        tmp = input("Enter yes or another path(absloute):")
        if tmp.lower() != "yes" and tmp.lower() != "y":
            localpath = tmp
        for item in dllist:
            fp = localpath + _pattern
            fp = fp.replace("$subjName", item.subjectName)
            fp = fp.replace("$lessonName", item.sessionName.replace(item.subjectName + "(", "").replace(")", ""))
            fp = fp.replace("$classDate", item.classDate)
            if not os.path.exists(fp):
                os.makedirs(fp)
            fn1 = _fnClass
            download(item.video1.url, fp + fn1, "%s → %s" % (item.video1.videoName, fp + fn1))
            fn2 = _fnScreen
            download(item.video2.url, fp + fn2, "%s → %s" % (item.video2.videoName, fp + fn2))
        print("OK. Please check your IDM.")
        print("You may have to start downloading manually")
    if int(menuc) == 2:
        if os.name != 'nt':
            print("Sorry, only Windows is supported:(")
        else:
            idmpath = "C:\\Program Files (x86)\\Internet Download Manager\\IDMan.exe"
            while not os.path.exists(idmpath) or not idmpath.lower().endswith("idman.exe"):
                print("IDM Not Found or invalid in %s" % idmpath)
                input("Enter IDMan.exe Path:")
            localpath = "D:\\downloads\\"
            print("Save to %s ?" % localpath)
            tmp = input("Enter yes or another path(absloute):")
            if tmp.lower() != "yes" and tmp.lower() != "y":
                localpath = tmp
            for item in dllist:
                fp = localpath + _pattern
                fp = fp.replace("$subjName", item.subjectName)
                fp = fp.replace("$lessonName", item.sessionName.replace(item.subjectName + "(", "").replace(")", ""))
                fp = fp.replace("$classDate", item.classDate)
                fn1 = _fnClass
                print("%s → %s" % (item.video1.videoName, fp + fn1))
                subprocess.call([idmpath, '/d', item.video1.url, '/p', fp, '/f', fn1, '/a', '/s'])
                fn2 = _fnScreen
                print("%s → %s" % (item.video1.videoName, fp + fn2))
                subprocess.call([idmpath, '/d', item.video2.url, '/p', fp, '/f', fn2, '/a', '/s'])
    if int(menuc) == 3:
        dic = {"data": []}
        for idx, item in enumerate(dllist):
            dic["data"].append(item._asdict())
            dic["data"][idx]["video1"] = item.video1._asdict()
            dic["data"][idx]["video2"] = item.video2._asdict()
        t = int(time.time())
        fn = "%s.json" % t
        with open(fn, "w", encoding="utf8") as json_file:
            json.dump(dic, json_file, ensure_ascii=False)
            json_file.close()
        print("Saved to %s" % fn)
    if int(menuc) == 4:
        t = int(time.time())
        fn = "%s.txt" % t
        with open(fn, "w", encoding="utf-8") as f:
            for item in dllist:
                print(item.video1.url)
                f.write(item.video1.url + "\n")
                print(item.video2.url)
                f.write(item.video2.url + "\n")
            f.close()
        print("Saved to %s" % fn)
    if int(menuc) == 5:
        print("Notice: The links are not permanent, they may expire in 1 day")
        cursubj = ""
        s = "# 视频列表"
        for item in dllist:
            if cursubj != item.subjectName:
                cursubj = item.subjectName
                curteacher = item.teacherName
                s = s + "\n\n## %s %s\n" % (cursubj, curteacher)
            lessonname = item.sessionName.replace(cursubj + "(", "").replace(")", "")
            s = s + "\n<details>\n<summary>%s</summary>\n<p style=\"padding-left: 2em;\">\n日期：%s<br />\n<a href=\"%s\" target=\"_blank\" rel=\"noopener noreferrer\">课堂</a><br />\n<a href=\"%s\" target=\"_blank\" rel=\"noopener noreferrer\">屏幕</a><br />\n</p>\n</details>" % (
                lessonname, item.classDate, item.video1.url, item.video2.url)
        t = int(time.time())
        fn = "%s.md" % t
        f = open(fn, "w")
        f.write(s)
        f.close()
        print("Saved to %s" % fn)


if __name__ == '__main__':
    main()
