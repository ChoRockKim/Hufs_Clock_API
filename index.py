from fastapi import FastAPI, Response
from fastapi.middleware.cors import CORSMiddleware
import requests
from bs4 import BeautifulSoup
from datetime import datetime, timedelta
import concurrent.futures
import calendar

# --- 기본 설정 ---
app = FastAPI()

# CORS 미들웨어 추가
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
}
HUFS_DOMAIN = "https://www.hufs.ac.kr"

# --- 크롤링 함수들 (디버깅 로그 추가) ---

def crawl_schedule():
    print("[DEBUG] crawl_schedule: 시작")
    try:
        response = requests.get(f"{HUFS_DOMAIN}/hufs/index.do#section4", headers=HEADERS)
        print(f"[DEBUG] crawl_schedule: 메인 페이지 응답 상태 코드: {response.status_code}")
        response.raise_for_status()
        soup = BeautifulSoup(response.text, 'html.parser')
        
        schedule_link = soup.select_one('#top_k2wiz_GNB_11360')
        if not schedule_link:
            print("[DEBUG] crawl_schedule: 학사일정 링크를 찾지 못함")
            raise ValueError("학사일정 링크 CSS 선택자를 찾을 수 없습니다.")
        
        print(f"[DEBUG] crawl_schedule: 찾은 학사일정 링크: {schedule_link.get('href')}")
        schedule_url = HUFS_DOMAIN + schedule_link['href']
        schedule_response = requests.get(schedule_url, headers=HEADERS)
        print(f"[DEBUG] crawl_schedule: 학사일정 페이지 응답 상태 코드: {schedule_response.status_code}")
        schedule_response.raise_for_status()
        
        schedule_soup = BeautifulSoup(schedule_response.text, 'html.parser')
        content_wrap = schedule_soup.find('div', class_='wrap-contents')
        if not content_wrap:
            print("[DEBUG] crawl_schedule: 학사일정 콘텐츠 영역을 찾지 못함")
            raise ValueError("학사일정 콘텐츠 영역을 찾을 수 없습니다.")
        
        schedule_dates = _extract_schedule_dates(content_wrap.find_all('li'))
        print(f"[DEBUG] crawl_schedule: 크롤링 성공, 데이터: {schedule_dates}")
        return schedule_dates
    except Exception as e:
        print(f"[ERROR] crawl_schedule: 크롤링 중 오류 발생: {e}")
        return {}

def _extract_schedule_dates(content_list):
    # ... (이하 동일)
    schedule_dates = {}
    for item in content_list:
        date_elems = item.find_all('p', class_='list-date')
        event_elems = item.find_all('p', class_='list-content')
        for date, event in zip(date_elems, event_elems):
            date_str = date.get_text(strip=True).split('~')[-1].strip()
            event_str = event.get_text(strip=True)
            if '제1학기 개강' in event_str: schedule_dates['first_start'] = date_str
            elif '제1학기 기말시험' in event_str: schedule_dates['first_end'] = date_str
            elif '제2학기 개강' in event_str: schedule_dates['second_start'] = date_str
            elif '제2학기 기말시험' in event_str: schedule_dates['second_end'] = date_str
    return schedule_dates

def crawl_notices(url):
    print(f"[DEBUG] crawl_notices: 시작, URL: {url}")
    try:
        response = requests.get(url, headers=HEADERS)
        print(f"[DEBUG] crawl_notices: 응답 상태 코드: {response.status_code}")
        response.raise_for_status()
        soup = BeautifulSoup(response.text, 'html.parser')
        notice_rows = soup.select("tbody tr:not(.notice)")
        print(f"[DEBUG] crawl_notices: 찾은 공지사항 행 개수: {len(notice_rows)}")
        
        notices = []
        for row in notice_rows[:10]:
            title_td = row.find('td', class_='td-subject')
            date_td = row.find('td', class_='td-date')
            if not (title_td and date_td and title_td.find('a')): continue
            link = title_td.find('a')['href']
            title = title_td.find('a').get_text(strip=True)
            date = date_td.get_text(strip=True)
            notices.append({'date': date, 'title': title, 'link': HUFS_DOMAIN + link})
        
        print(f"[DEBUG] crawl_notices: 크롤링 성공, {len(notices)}개 공지사항 추출")
        return notices
    except Exception as e:
        print(f"[ERROR] crawl_notices: 크롤링 중 오류 발생: {e}")
        return []

def crawl_meals():
    print("[DEBUG] crawl_meals: 시작")
    try:
        today = datetime.now()
        start_of_week = today - timedelta(days=today.weekday())
        end_of_week = start_of_week + timedelta(days=6)

        # selMonth가 0-indexed가 아닌 1-indexed로 추정되므로 수정
        payload = {
            "selCafId": "h101",
            "selWeekFirstDay": start_of_week.day,
            "selWeekLastDay": end_of_week.day,
            "selYear": today.year,
            "selMonth": today.month 
        }
        print(f"[DEBUG] crawl_meals: API 요청 페이로드: {payload}")

        api_url = "https://www.hufs.ac.kr/cafeteria/hufs/1/getMenu.do"
        response = requests.post(api_url, data=payload, headers=HEADERS)
        print(f"[DEBUG] crawl_meals: API 응답 상태 코드: {response.status_code}")
        response.raise_for_status()
        
        soup = BeautifulSoup(response.text, 'html.parser')
        meal_rows = soup.find_all('tr')
        print(f"[DEBUG] crawl_meals: 찾은 학식 메뉴 행 개수: {len(meal_rows)}")
        
        meals = []
        for row in meal_rows:
            th = row.find('th')
            tds = row.find_all('td')
            if not th or not tds: continue
            meal_time = th.get_text(strip=True)
            menus = []
            for td in tds:
                strong_tag = td.find('strong', class_='point')
                pay_tag = td.find('p', class_='pay')
                
                menu_name = strong_tag.get_text(strip=True) if strong_tag else ''
                price = pay_tag.get_text(strip=True) if pay_tag else ''
                
                menus.append({
                    "name": menu_name,
                    "price": price
                })
            meals.append({'time': meal_time, 'menus': menus})
        
        print(f"[DEBUG] crawl_meals: 크롤링 성공, {len(meals)}개 식사 시간대 추출")
        return meals
    except Exception as e:
        print(f"[ERROR] crawl_meals: 크롤링 중 오류 발생: {e}")
        return []

# --- API 엔드포인트 ---
@app.get("/api/data")
def get_all_data(response: Response):
    response.headers["Cache-Control"] = "public, s-maxage=43200"
    with concurrent.futures.ThreadPoolExecutor() as executor:
        future_schedule = executor.submit(crawl_schedule)
        future_general_notices = executor.submit(crawl_notices, url="https://www.hufs.ac.kr/hufs/11281/subview.do")
        future_haksa_notices = executor.submit(crawl_notices, url="https://www.hufs.ac.kr/hufs/11282/subview.do")
        future_meals = executor.submit(crawl_meals)
        schedule = future_schedule.result()
        general_notices = future_general_notices.result()
        haksa_notices = future_haksa_notices.result()
        meals = future_meals.result()
    all_notices = sorted(general_notices + haksa_notices, key=lambda x: x.get('date', '0000-00-00'), reverse=True)
    return {"schedule": schedule, "notices": all_notices, "meals": meals, "timestamp": datetime.now().isoformat()}

@app.get("/")
def root():
    return {"message": "HUFS Clock API"}