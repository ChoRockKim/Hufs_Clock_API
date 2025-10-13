from fastapi import FastAPI, Response
import requests
from bs4 import BeautifulSoup
from datetime import datetime, timedelta
import concurrent.futures
import calendar

# --- 기본 설정 ---
app = FastAPI()
HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
}
HUFS_DOMAIN = "https://www.hufs.ac.kr"

# --- 크롤링 함수들 ---

def crawl_schedule():
    """학사일정 크롤링"""
    try:
        # 메인 페이지에서 학사일정 링크 추출
        response = requests.get(f"{HUFS_DOMAIN}/hufs/index.do#section4", headers=HEADERS)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, 'html.parser')
        
        schedule_link = soup.select_one('#top_k2wiz_GNB_11360')
        if not schedule_link:
            raise ValueError("학사일정 링크 CSS 선택자를 찾을 수 없습니다.")

        # 학사일정 페이지 크롤링
        schedule_url = HUFS_DOMAIN + schedule_link['href']
        schedule_response = requests.get(schedule_url, headers=HEADERS)
        schedule_response.raise_for_status()
        
        schedule_soup = BeautifulSoup(schedule_response.text, 'html.parser')
        content_wrap = schedule_soup.find('div', class_='wrap-contents')
        if not content_wrap:
            raise ValueError("학사일정 콘텐츠 영역을 찾을 수 없습니다.")
        
        # 학사일정 추출
        schedule_dates = _extract_schedule_dates(content_wrap.find_all('li'))
        return schedule_dates
    except Exception as e:
        print(f"학사일정 크롤링 실패: {e}")
        return {} # 실패 시 빈 객체 반환

def _extract_schedule_dates(content_list):
    schedule_dates = {}
    for item in content_list:
        date_elems = item.find_all('p', class_='list-date')
        event_elems = item.find_all('p', class_='list-content')
        
        for date, event in zip(date_elems, event_elems):
            date_str = date.get_text(strip=True).split('~')[-1].strip()
            event_str = event.get_text(strip=True)
            
            if '제1학기 개강' in event_str:
                schedule_dates['first_start'] = date_str
            elif '제1학기 기말시험' in event_str:
                schedule_dates['first_end'] = date_str
            elif '제2학기 개강' in event_str:
                schedule_dates['second_start'] = date_str
            elif '제2학기 기말시험' in event_str:
                schedule_dates['second_end'] = date_str
    return schedule_dates

def crawl_notices(url):
    """공지사항 크롤링 (일반/학사 공용)"""
    try:
        response = requests.get(url, headers=HEADERS)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, 'html.parser')
        notice_rows = soup.select("tbody tr:not(.notice)") # 공지 아닌 일반 게시물
        
        notices = []
        for row in notice_rows[:10]: # 최근 10개
            title_td = row.find('td', class_='td-subject')
            date_td = row.find('td', class_='td-date')
            
            if not (title_td and date_td and title_td.find('a')):
                continue

            link = title_td.find('a')['href']
            title = title_td.find('a').get_text(strip=True)
            date = date_td.get_text(strip=True)
            
            notices.append({
                'date': date,
                'title': title,
                'link': HUFS_DOMAIN + link
            })
        return notices
    except Exception as e:
        print(f"{url} 공지사항 크롤링 실패: {e}")
        return []

def crawl_meals():
    """학식 메뉴 크롤링 (Selenium 대체)"""
    try:
        today = datetime.now()
        # 이번 주 월요일과 일요일 날짜 계산
        start_of_week = today - timedelta(days=today.weekday())
        end_of_week = start_of_week + timedelta(days=6)

        payload = {
            "selCafId": "h101",
            "selWeekFirstDay": start_of_week.day,
            "selWeekLastDay": end_of_week.day,
            "selYear": today.year,
            "selMonth": today.month -1 # API가 0-indexed month를 사용하는 것으로 추정
        }
        
        # 월이 바뀌는 주 처리
        if end_of_week.month != today.month:
             # 다음달로 넘어가는 주일 경우, 일단 오늘 월 기준으로 요청
             pass # 특별한 로직이 필요하다면 여기에 추가

        api_url = "https://www.hufs.ac.kr/cafeteria/hufs/1/getMenu.do"
        response = requests.post(api_url, data=payload, headers=HEADERS)
        response.raise_for_status()
        
        # 응답이 HTML이므로 BeautifulSoup으로 파싱
        soup = BeautifulSoup(response.text, 'html.parser')
        
        meals = []
        meal_rows = soup.find_all('tr')
        
        for row in meal_rows:
            th = row.find('th')
            tds = row.find_all('td')
            
            if not th or not tds:
                continue
            
            meal_time = th.get_text(strip=True)
            menus = []
            for td in tds:
                menu_text = td.get_text(separator='\n', strip=True)
                menus.append(menu_text)

            meals.append({
                'time': meal_time,
                'menus': menus
            })
        return meals
    except Exception as e:
        print(f"학식 크롤링 실패: {e}")
        return []

# --- API 엔드포인트 ---

@app.get("/api/data")
def get_all_data(response: Response):
    """모든 데이터를 병렬로 크롤링하여 JSON으로 반환"""
    
    # Vercel의 CDN 캐시를 12시간으로 설정
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

    # 공지사항 통합 및 정렬
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
    return {"message": "HUFS Clock API"}
