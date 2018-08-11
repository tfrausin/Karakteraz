import atexit
import logging
import os
import smtplib
import sys
from datetime import datetime
from email.mime.image import MIMEImage
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

import durationpy
import yaml
from apscheduler.schedulers.blocking import BlockingScheduler
from requests import post, get
from selenium import webdriver
from selenium.webdriver.common.action_chains import ActionChains
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait


class App:
    def __init__(self):
        self.logger = logging.getLogger(App.__name__)
        self.logger.info('launched Karakteraz')

        try:
            with open('app.yml', 'r') as stream:
                self.configuration = yaml.load(stream)['karakteraz']
        except yaml.YAMLError:
            self.logger.exception('configuration format error')
            sys.exit(1)
        except FileNotFoundError:
            self.logger.exception('configuration not found')
            sys.exit(1)

        self.configuration['notification']['email'] = None if sum(
            [1 for _, v in self.configuration['notification']['email'].items() if v is None]) > 0 else \
            self.configuration['notification']['email']
        self.configuration['notification']['telegram'] = None if sum(
            [1 for _, v in self.configuration['notification']['telegram'].items() if v is None]) > 0 else \
            self.configuration['notification']['telegram']
        self.configuration['watch-list'] = [x.upper() for x in self.configuration['watch-list']]
        self.configuration['results-page'] = 'https://fsweb.no/studentweb/resultater.jsf'
        self.logger.info(
            'installed configuration:\n{}'.format(yaml.dump(self.configuration, default_flow_style=False)))

        self.interval = durationpy.from_str(self.configuration['frequency']).total_seconds()
        self.driver = webdriver.Firefox()
        self.driver.wait = WebDriverWait(self.driver, 10)
        self.scheduler = BlockingScheduler()

    def start(self):
        self.scheduler.add_job(self.trigger_schedule, trigger='cron', hour='6,23')
        job = self.scheduler.add_job(fetch_grades, id='fetch', trigger='interval', seconds=self.interval,
                                     args=[self.driver, self.logger, self.configuration], coalesce=True)

        time = datetime.now()
        if time.hour >= 23 or time.hour < 6:
            job.pause()

        atexit.register(lambda: self.scheduler.shutdown(wait=False))
        atexit.register(lambda: self.driver.quit())
        self.scheduler.start()


    def trigger_schedule(self):
        job = self.scheduler.get_job('fetch')
        if datetime.now().hour == 6:
            job.resume()
            self.logger.info('ready for a new days work. Resuming scheduled tasks')
        else:
            job.pause()
            self.logger.info('sleeping for the night.')


def fetch_grades(driver, logger, conf):
    logger.info('invoked fetch grades')

    result_page, signin_page, watchlist = conf['results-page'], conf['target'], conf['watch-list']
    username, password, university = conf['feide']['username'], conf['feide']['password'], conf['university']

    driver.get(result_page)
    authenticated = driver.current_url == result_page

    if not authenticated:
        logger.info('received redirect to {}, sessions is expired. Attempting to establish new connection'
                    .format(driver.current_url))
        try:
            authenticate(driver, signin_page, username, password, university)
        except Exception:
            logger.exception("encountered unexpected exception when attempting to authenticate")
            logger.info("unable to authenticate. Verify configuration parameters. Aborting this attempt ...")
            return

        logger.info('successfully authenticated with provider. Fetching grades ...')
        driver.get(result_page)

    driver.wait.until(EC.presence_of_element_located((By.ID, 'resultatlisteForm:HeleResultater:panel')))
    match = filter(lambda x: x.text.upper() in watchlist, driver.find_elements_by_class_name('infoLinje'))

    if not match:
        logger.info('found no matches. No grades in [{}] have been published yet ...'.format(
            '[%s]' % ', '.join(map(str, watchlist))))
        return

    for m in match:
        path = './capture.png'
        ActionChains(driver).move_to_element(m)
        driver.save_screenshot(path)
        notify(m.text, path, conf['notification']['email'], conf['notification']['telegram'], logger)

        watchlist = [x for x in watchlist if x != m.text]
        logger.info('removed {} from watch list'.format(m.text))
        conf['watch-list'] = watchlist
        if not watchlist:
            logger.info('watchlist is empty. Terminating ...')
            sys.exit(0)


def authenticate(driver, target, username, password, university):
    driver.get(target)
    driver.wait.until(EC.presence_of_element_located((By.PARTIAL_LINK_TEXT, 'Log on using Feide'))).click()
    driver.wait.until(EC.presence_of_element_located((By.XPATH, '//label[@for="org_selector-selectized"]'))).click()
    driver.wait.until(EC.presence_of_all_elements_located((By.XPATH, '//input[@type="text"]')))[1].send_keys(
        university + u'\ue007')
    driver.wait.until(EC.presence_of_element_located((By.ID, 'username'))).send_keys(username)
    driver.find_element_by_id('password').send_keys(password)
    driver.find_element_by_xpath('//button[@type="submit"]').submit()
    driver.wait.until(EC.presence_of_element_located((By.ID, 'menuOpener')))


def notify(match, path, email, telegram, logger):
    content = 'You have received your grade for course {}'.format(match)

    if email is not None:
        def send():
            try:
                provider, port = email['provider'].split(':')
            except ValueError:
                logger.exception('configuration error, unable to send email')
                return

            if int(port) == 465:
                email_provider = smtplib.SMTP_SSL(provider, str(port))
            else:
                email_provider = smtplib.SMTP(provider, port)
                email_provider.ehlo()
                email_provider.starttls()
            email_provider.login(email['username'], email['password'])

            f = open(path, 'rb')
            msg = MIMEMultipart()
            msg['Subject'] = content
            msg['From'] = email['from']
            msg['To'] = email['to']
            msg.attach(MIMEText(content))
            msg.attach(MIMEImage(f.read(), name=os.path.basename(path)))
            email_provider.sendmail(msg['From'], msg['To'], msg.as_string())
            email_provider.quit()
            f.close()
            logger.info('notified user on email for newly published grade for course {}'.format(match))

        send()

    if telegram is not None:
        base = 'https://api.telegram.org/bot{}/'.format(telegram['token'])

        r = get('{}sendMessage?chat_id={}&text={}'.format(base, telegram['chat-id'], content))

        if r.status_code != 200:
            logger.info('failed to send notification on telegram: {}'.format(r.reason))
            return
        r = post('{}sendPhoto'.format(base), data={'chat_id': telegram['chat-id']}, files={'photo': open(path, 'rb')})

        if r.status_code != 200:
            logger.info('failed to send notification on telegram: {}'.format(r.reason))
            return


if __name__ == "__main__":
    logging.basicConfig(
        format='%(asctime)s [%(threadName)-12.12s] [%(levelname)-5.5s]  %(message)s',
        handlers=[
            logging.FileHandler(filename='/var/log/karakteraz/app.log', mode='a+'),
            logging.StreamHandler(sys.stdout)
        ],
        level=logging.DEBUG)
    App().start()
