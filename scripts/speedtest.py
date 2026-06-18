import requests
import time


def downloadtest(url):
    starttime = time.time()
    response = requests.get(url)
    totaldownloaded = len(response.content)

    endtime = time.time()
    elapsedtime = endtime - starttime
    downloadspeed = (
        totaldownloaded / (1024 * 1024) / elapsedtime
    )  # Convert to Mbps

    return downloadspeed


# Example URL for a large file
url = "http://speedtest.tele2.net/100MB.zip"
downloadspeed = downloadtest(url)
print(f"Download Speed: {downloadspeed:.2f} Mbps")
