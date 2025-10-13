# -*- coding: utf-8 -*- 

from fastapi import FastAPI, Response
from fastapi.middleware.cors import CORSMiddleware
import requests
from bs4 import BeautifulSoup
from datetime import datetime, timedelta
import concurrent.futures
from typing import List, Dict, Any

# ===============================================================================
# 1. 기본 설정 (FastAPI 앱, 미들웨어, 상수)
# ===============================================================================

app = FastAPI()

# CORS(Cross-Origin Resource Sharing) 미들웨어 설정
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
}
HUFS_DOMAIN = "https://www.hufs.ac.kr"

# ===============================================================================
# 2. 크롤링 함수
# ===============================================================================

def crawl_schedule() -> Dict[str, str]:
    """HUFS 웹사이트에서 학사일정을 크롤링합니다."""
    try:
        response = requests.get(f"{HUFS_DOMAIN}/hufs/index.do#section4", headers=HEADERS)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, 'html.parser')
        
        schedule_link = soup.select_one('#top_k2wiz_GNB_11360')
        if not schedule_link:
            raise ValueError("학사일정 링크 CSS 선택자를 찾을 수 없습니다.")

        schedule_url = HUFS_DOMAIN + schedule_link['href']
        schedule_response = requests.get(schedule_url, headers=HEADERS)
        schedule_response.raise_for_status()
        
        schedule_soup = BeautifulSoup(schedule_response.text, 'html.parser')
        content_wrap = schedule_soup.find('div', class_='wrap-contents')
        if not content_wrap:
            raise ValueError("학사일정 콘텐츠 영역을 찾을 수 없습니다.")
        
        return _extract_schedule_dates(content_wrap.find_all('li'))
    except Exception as e:
        print(f"[API ERROR] 학사일정 크롤링 실패: {e}")
        return {}

def _extract_schedule_dates(items: List[Any]) -> Dict[str, str]:
    """학사일정 HTML 리스트에서 주요 일정(개강, 종강)의 날짜를 추출합니다."""
    schedule_dates = {}
    for item in items:
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

def crawl_notices(url: str) -> List[Dict[str, str]]:
    """HUFS 웹사이트에서 일반 또는 학사 공지사항을 크롤링합니다."""
    try:
        response = requests.get(url, headers=HEADERS)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, 'html.parser')
        notice_rows = soup.select("tbody tr:not(.notice)")
        
        notices = []
        for row in notice_rows[:10]:
            title_td = row.find('td', class_='td-subject')
            date_td = row.find('td', class_='td-date')
            if not (title_td and date_td and title_td.find('a')): continue
            
            link = title_td.find('a')['href']
            title = title_td.find('a').get_text(strip=True)
            date = date_td.get_text(strip=True)
            notices.append({'date': date, 'title': title, 'link': HUFS_DOMAIN + link})
        return notices
    except Exception as e:
        print(f"[API ERROR] 공지사항 크롤링 실패 (URL: {url}): {e}")
        return []

def crawl_meals() -> List[Dict[str, Any]]:
    """HUFS 학식 API를 호출하여 이번 주 학식 메뉴를 가져옵니다."""
    try:
        today = datetime.now()
        start_of_week = today - timedelta(days=today.weekday())
        end_of_week = start_of_week + timedelta(days=6)

        payload = {
            "selCafId": "h101",
            "selWeekFirstDay": start_of_week.day,
            "selWeekLastDay": end_of_week.day,
            "selYear": today.year,
            "selMonth": today.month
        }

        api_url = "https://www.hufs.ac.kr/cafeteria/hufs/1/getMenu.do"
        response = requests.post(api_url, data=payload, headers=HEADERS)
        response.raise_for_status()
        
        soup = BeautifulSoup(response.text, 'html.parser')
        meal_rows = soup.find_all('tr')
        
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

                if strong_tag:
                    menu_name = strong_tag.get_text(strip=True)
                else:
                    if pay_tag: pay_tag.decompose()
                    menu_name = td.get_text(separator='\n', strip=True)
                
                price = pay_tag.get_text(strip=True) if pay_tag else ''
                menus.append({"name": menu_name, "price": price})
            
            meals.append({'time': meal_time, 'menus': menus})
        return meals
    except Exception as e:
        print(f"[API ERROR] 학식 크롤링 실패: {e}")
        return []

# ===============================================================================
# 3. API 엔드포인트
# ===============================================================================

@app.get("/api/data")
def get_all_data(response: Response):
    """모든 크롤링 함수를 병렬로 실행하고 결과를 종합하여 반환하는 메인 엔드포인트"""
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

    all_notices = sorted(
        general_notices + haksa_notices,
        key=lambda x: x.get('date', '0000-00-00'),
        reverse=True
    )

    return {
        "schedule": schedule,
        "notices": all_notices,
        "meals": meals,
        "timestamp": datetime.now().isoformat()
    }

@app.get("/")
def root():
    """API 서버의 상태를 확인하기 위한 기본 엔드포인트"""
    return {"message": "HUFS Clock API is running."}
