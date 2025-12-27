# -*- coding: utf-8 -*- 
import os
from dotenv import load_dotenv
from fastapi import FastAPI, Response, Query
from fastapi.middleware.cors import CORSMiddleware
import requests
from bs4 import BeautifulSoup
from datetime import datetime, timedelta, timezone
from typing import List, Dict, Any

load_dotenv()

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

# 기상청 API 키
OPENWEATHER_API_KEY = os.getenv('WEATHER_SERVICE_KEY')

# 캠퍼스별 좌표
CAMPUS_AXIS = {
    'SEOUL' : {'nx' : 61, 'ny' : 127},
    'GLOBAL' : {'nx' : 65, 'ny' : 122}
}

# 캠퍼스 별 도서관 요청 param
LIBRARY_CONFIG = {
    'SEOUL' : {
        'api_path' : '1',
        'branch_group_id' : '1'
    },
    'GLOBAL' : {
        'api_path' : '2',
        'branch_group_id' : '2'
    }
}

# 요청시간 계산 함수 (초단기실황용)
def get_base_time():
    # 한국 시간대 설정
    kst = timezone(timedelta(hours=9))
    now = datetime.now(kst)
    
    current_hour = now.hour
    current_minute = now.minute
    
    # 현재 분이 10분 미만이면 1시간 전 데이터 사용
    # 예: 08:05 → 08:10 이전 → 07:00 데이터
    # 예: 08:15 → 08:10 이후 → 08:00 데이터
    if current_minute < 10:
        # 1시간 전으로 이동
        base_time_dt = now - timedelta(hours=1)
    else:
        # 현재 시간 사용
        base_time_dt = now
    
    base_date = base_time_dt.strftime('%Y%m%d')
    base_time = base_time_dt.strftime('%H00')
    
    return base_date, base_time

# 단기예보용 base_time 계산 함수
def get_forecast_base_time():
    """단기예보 발표시각 계산 (0200, 0500, 0800, 1100, 1400, 1700, 2000, 2300)"""
    kst = timezone(timedelta(hours=9))
    now = datetime.now(kst)
    
    # 단기예보 발표시각 (1일 8회)
    base_times = [2, 5, 8, 11, 14, 17, 20, 23]
    current_hour = now.hour
    current_minute = now.minute
    
    base_hour = None
    
    # 역순으로 확인하여 가장 최근의 사용 가능한 base_time 찾기
    for bt in reversed(base_times):
        # 현재 시간이 base_time:10 이후인지 확인
        if current_hour > bt or (current_hour == bt and current_minute >= 10):
            base_hour = bt
            break
    
    # 현재 시간이 02:10 이전이면 전날 23:00 사용
    if base_hour is None:
        yesterday = now - timedelta(days=1)
        base_date = yesterday.strftime('%Y%m%d')
        base_time = "2300"
    else:
        base_date = now.strftime('%Y%m%d')
        base_time = f"{base_hour:02d}00"
    
    return base_date, base_time

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
        response = requests.get(f"{HUFS_DOMAIN}/hufs/index.do#section4", headers=HEADERS, timeout=5)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, 'html.parser')
        
        schedule_link = soup.select_one('#top_k2wiz_GNB_11360')
        if not schedule_link:
            raise ValueError("학사일정 링크 CSS 선택자를 찾을 수 없습니다.")

        schedule_url = HUFS_DOMAIN + schedule_link['href']
        schedule_response = requests.get(schedule_url, headers=HEADERS, timeout=5)
        schedule_response.raise_for_status()
        
        schedule_soup = BeautifulSoup(schedule_response.text, 'html.parser')
        content_wrap = schedule_soup.find('div', class_='wrap-contents')
        if not content_wrap:
            raise ValueError("학사일정 콘텐츠 영역을 찾을 수 없습니다.")
        
        schedule_dates = _extract_schedule_dates(content_wrap.find_all('li'))
        print("Crawled schedule:", schedule_dates)
        return schedule_dates

    except Exception as e:
        print("Error crawling schedule:", str(e))
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
        response = requests.get(url, headers=HEADERS, timeout=5)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, 'html.parser')
        notice_rows = soup.select("tbody tr:not(.notice)")
        
        notices = []
        for row in notice_rows[:10]:
            title_td = row.find('td', class_='td-subject')
            date_td = row.find('td', class_='td-date')
            if not (title_td and date_td and title_td.find('a')): continue

            a_tag = title_td.find('a')
            link = a_tag['href']
            date = date_td.get_text(strip=True)

            # <strong> 태그에서 제목 추출
            strong_tag = a_tag.find('strong')
            title = strong_tag.get_text(strip=True) if strong_tag else a_tag.get_text(strip=True)

            # "new" 클래스를 가진 <span> 태그 확인 후 " (NEW)" 추가
            if a_tag.find('span', class_='new'):
                title += " (NEW)"
            
            notices.append({'date': date, 'title': title, 'link': HUFS_DOMAIN + link})
        print("Crawled notices from", url, ":", notices)
        return notices
    except Exception as e:
        print("Error crawling notices from", url, ":", str(e))
        return []

def _crawl_meals_by_campus(campus_path: str) -> List[Dict[str, Any]]:
    """HUFS 학식 API를 호출하여 이번 주 학식 메뉴를 가져옵니다."""
    print(f"\n\n[!!!] Attempting to crawl meals for campus_path: {campus_path} [!!!]\n\n")
    try:
        today = datetime.now()
        # 식당 페이지와 동일하게 월요일~토요일 범위로 계산
        start_of_week = today - timedelta(days=today.weekday())  # 월요일
        end_of_week = start_of_week + timedelta(days=5)  # 토요일

        # 캠퍼스별 식당 ID 설정
        caf_id = "h101" if campus_path == "1" else "h203"

        payload = {
            "selCafId": caf_id,
            "selWeekFirstDay": start_of_week.day,
            "selWeekLastDay": end_of_week.day,
            "selYear": today.year,
            "selMonth": today.month  # 페이지는 현재 월을 그대로 전송
        }

        api_url = f"https://www.hufs.ac.kr/cafeteria/hufs/{campus_path}/getMenu.do"
        response = requests.post(api_url, data=payload, headers=HEADERS, timeout=5)
        response.raise_for_status()
        
        soup = BeautifulSoup(response.text, 'html.parser')
        meal_rows = soup.find_all('tr')
        
        meals = []
        for row in meal_rows:
            th = row.find('th')
            tds = row.find_all('td')
            if not th or not tds: continue

            meal_time = th.get_text(strip=True)
            print(f"\n[Debug] Processing meal time: '{meal_time}'")
            menus = []
            for td in tds:
                pay_tag = td.find('p', class_='pay')
                menu_name = ""

                # 글로벌캠퍼스 '이벤트 데이' 특별 처리
                event_day_tag = td.select_one('ul > li:nth-child(1) > strong.point')
                if campus_path == "2" and event_day_tag and "** 이벤트 데이 **" in event_day_tag.get_text(strip=True):
                    # 이벤트 데이는 두 번째 li에 strong 태그 없이 메뉴가 있음
                    event_menu_li = td.select_one('ul > li:nth-child(2)')
                    if event_menu_li:
                        menu_name = event_menu_li.get_text(separator='\n', strip=True)
                else:
                    # 일반적인 경우 (기존 로직)
                    menu_items_li = td.select('ul > li')
                    if menu_items_li:
                        # strong.point 태그가 없을 때를 대비해 li 텍스트 전체를 폴백으로 사용
                        strong_texts = [s.get_text(strip=True) for s in td.select('ul > li > strong.point')]
                        if strong_texts:
                            menu_name = '\n'.join(strong_texts)
                        else:
                            menu_name = '\n'.join(li.get_text(separator=' ', strip=True) for li in menu_items_li)
                    else:
                        # ul > li 구조가 없는 경우를 위한 폴백
                        if pay_tag: pay_tag.decompose()
                        menu_name = td.get_text(separator='\n', strip=True)

                price = pay_tag.get_text(strip=True) if pay_tag else ''
                
                print(f"  > Found menu for one day: '{menu_name.strip()}'")

                # 메뉴가 없는 경우 제외
                if "등록된 메뉴가" in menu_name or not menu_name.strip():
                    print(f"    - Skipping: Menu is empty or marked as not registered.")
                    continue
                
                menus.append({"name": menu_name, "price": price})
                print(f"    - Success: Added to the list for '{meal_time}'.")
            
            meals.append({'time': meal_time, 'menus': menus})
        print(f"Crawled meals for campus {campus_path}:", meals)
        return meals
    except Exception as e:
        print(f"Error crawling meals for campus {campus_path}:", str(e))
        return []

def crawl_meals() -> List[Dict[str, Any]]:
    """인문캠퍼스 학식 메뉴를 크롤링합니다."""
    return _crawl_meals_by_campus("1")

def crawl_global_meals() -> List[Dict[str, Any]]:
    """글로벌캠퍼스 학식 메뉴를 크롤링합니다."""
    return _crawl_meals_by_campus("2")


def _debug_print_meals(campus_label: str, meals: List[Dict[str, Any]]) -> None:
    """학식 목록을 콘솔에 보기 좋게 출력합니다."""
    print(f"\n--- Meal debug ({campus_label}) ---")
    if not meals:
        print("No meals found.")
        return
    for meal in meals:
        print(f"[{meal.get('time')}]")
        if not meal.get('menus'):
            print("  - (empty)")
            continue
        for idx, item in enumerate(meal['menus'], start=1):
            name = item.get('name', '').replace('\n', ' | ')
            price = item.get('price', '')
            print(f"  {idx}. {name} ({price})")
    print("--- End meal debug ---\n")




# ===============================================================================
# 3. API 엔드포인트
# ===============================================================================

def _get_common_data():
    """공통 데이터(학사일정, 공지사항)를 크롤링하고 정렬합니다."""
    schedule = crawl_schedule()
    general_notices = crawl_notices(url="https://www.hufs.ac.kr/hufs/11281/subview.do")
    haksa_notices = crawl_notices(url="https://www.hufs.ac.kr/hufs/11282/subview.do")

    all_notices = sorted(
        general_notices + haksa_notices,
        key=lambda x: x.get('date', '0000-00-00'),
        reverse=True
    )
    return schedule, all_notices

@app.get("/api/data")
def get_all_data(response: Response):
    """인문캠퍼스 데이터를 반환합니다."""
    response.headers["Cache-Control"] = "public, s-maxage=60, stale-while-revalidate=60"
    schedule, all_notices = _get_common_data()
    meals = crawl_meals()
    _debug_print_meals("Humanities", meals)

    return {
        "schedule": schedule,
        "notices": all_notices,
        "meals": meals,
        "timestamp": datetime.now().isoformat()
    }

@app.get('/api/global/data')
def get_global_data(response: Response):
    """글로벌캠퍼스 데이터를 반환합니다."""
    response.headers["Cache-Control"] = "public, s-maxage=60, stale-while-revalidate=60"
    schedule, all_notices = _get_common_data()
    meals = crawl_global_meals()
    _debug_print_meals("Global", meals)

    data_to_return = {
        "schedule": schedule,
        "notices": all_notices,
        "meals": meals,
        "timestamp": datetime.now().isoformat()
    }
    
    print("--- Crawled data for /api/global/data ---")
    print(data_to_return)
    print("------------------------------------------")
    
    return data_to_return


@app.get("/api/library")
def get_library_seats(response: Response, campus: str = Query("SEOUL")): # 1. response 객체 받기

    # 캐시 시간 1분
    # s-maxage=60: 1분 동안은 저장된 거 보여줌 (학교 서버 보호)
    # stale-while-revalidate=60: 1분 지났으면 1분 더 옛날 거 보여주고 뒤에서 갱신
    response.headers["Cache-Control"] = "s-maxage=60, stale-while-revalidate=60"

    config = LIBRARY_CONFIG.get(campus.upper(), LIBRARY_CONFIG['SEOUL'])
    url = f"https://lib.hufs.ac.kr/pyxis-api/{config['api_path']}/seat-rooms?smufMethodCode=PC&roomTypeId=2&branchGroupId={config['branch_group_id']}"
    
    try:
        headers = {
            "User-Agent": "Mozilla/5.0 ..." 
        }
        response_data = requests.get(url, headers=headers, timeout=10) # 변수명 겹치지 않게 response -> response_data로 변경
        data = response_data.json()
        data['campus'] = campus.upper()

        return data

    except Exception as e:
        return {"success" : False, "message": str(e)}

@app.get("/api/weather")
def get_weather(campus: str = Query("SEOUL")):
    """날씨 정보를 반환합니다."""
    axis = CAMPUS_AXIS.get(campus, CAMPUS_AXIS['SEOUL'])
    nx = axis['nx']
    ny = axis['ny']

    # 초단기실황용 base_time
    base_date, base_time = get_base_time()
    # 단기예보용 base_time
    forecast_date, forecast_time = get_forecast_base_time()
    
    url_current = 'http://apis.data.go.kr/1360000/VilageFcstInfoService_2.0/getUltraSrtNcst'
    url_forecast = "http://apis.data.go.kr/1360000/VilageFcstInfoService_2.0/getVilageFcst"

    # 초단기실황 파라미터
    params_current = {
        'serviceKey': OPENWEATHER_API_KEY,
        'pageNo': '1',
        'numOfRows': '1000',
        'dataType': 'JSON',
        'base_date': base_date,
        'base_time': base_time,
        'nx': nx,
        'ny': ny
    }
    
    # 단기예보 파라미터
    params_forecast = {
        'serviceKey': OPENWEATHER_API_KEY,
        'pageNo': '1',
        'numOfRows': '1000',
        'dataType': 'JSON',
        'base_date': forecast_date,
        'base_time': forecast_time,
        'nx': nx,
        'ny': ny
    }

    try:
        # 초단기실황 API 호출
        response_current = requests.get(url_current, params=params_current, timeout=15)
        response_current.raise_for_status()
        data_current = response_current.json()
        
        # 응답 구조 검증
        if 'response' not in data_current or 'body' not in data_current['response']:
            raise ValueError("Invalid current weather API response structure")
        
        if 'items' not in data_current['response']['body'] or 'item' not in data_current['response']['body']['items']:
            raise ValueError("No items in current weather API response")
        
        items_current = data_current['response']['body']['items']['item']
        
        # items가 리스트가 아닌 경우 처리
        if not isinstance(items_current, list):
            items_current = [items_current]
        
        result = {
            "temp": "-",
            "humidity": "-",
            "rainType": "0",
            "sky": "-",      # 하늘상태
            "tmn": "-",      # 최저기온
            "tmx": "-"       # 최고기온
        }
        
        # 초단기실황 데이터 파싱
        for item in items_current:
            if item['category'] == 'T1H': # 기온
                result['temp'] = item['obsrValue']
            elif item['category'] == 'REH': # 습도
                result['humidity'] = item['obsrValue']
            elif item['category'] == 'PTY': # 강수형태
                result['rainType'] = item['obsrValue']
        
        # 단기예보 API 호출
        response_forecast = requests.get(url_forecast, params=params_forecast, timeout=15)
        response_forecast.raise_for_status()
        data_forecast = response_forecast.json()
        
        if 'response' in data_forecast and 'body' in data_forecast['response']:
            if 'items' in data_forecast['response']['body'] and 'item' in data_forecast['response']['body']['items']:
                items_forecast = data_forecast['response']['body']['items']['item']
                
                # items가 리스트가 아닌 경우 처리
                if not isinstance(items_forecast, list):
                    items_forecast = [items_forecast]
                
                # 단기예보 데이터 파싱
                kst = timezone(timedelta(hours=9))
                today = datetime.now(kst)
                today_str = today.strftime('%Y%m%d')
                tomorrow_str = (today + timedelta(days=1)).strftime('%Y%m%d')
                
                # SKY는 시간대별로 다르므로 최신 시간대 선택 (오늘 날짜 우선)
                sky_time = '0000'
                sky_date = ''
                # TMN, TMX는 하루에 한 번만 제공되므로 첫 번째 값 사용
                tmn_found = False
                tmx_found = False
                
                # 디버깅: TMN, TMX 항목 수집
                tmn_candidates = []
                tmx_candidates = []
                
                for item in items_forecast:
                    fcst_date = item.get('fcstDate', '')
                    fcst_time = item.get('fcstTime', '0000')
                    category = item.get('category', '')
                    
                    if category == 'SKY': # 하늘상태
                        # 오늘 날짜의 가장 최근 시간대 데이터 사용
                        if fcst_date == today_str and fcst_time > sky_time:
                            result['sky'] = item['fcstValue']
                            sky_time = fcst_time
                            sky_date = fcst_date
                    elif category == 'TMN': # 최저기온
                        # 오늘 또는 내일 날짜의 TMN 수집
                        if fcst_date in [today_str, tomorrow_str]:
                            tmn_candidates.append({
                                'date': fcst_date,
                                'time': fcst_time,
                                'value': item['fcstValue']
                            })
                            # 오늘 날짜의 TMN 우선 사용
                            if fcst_date == today_str and not tmn_found:
                                result['tmn'] = item['fcstValue']
                                tmn_found = True
                    elif category == 'TMX': # 최고기온
                        # 오늘 또는 내일 날짜의 TMX 수집
                        if fcst_date in [today_str, tomorrow_str]:
                            tmx_candidates.append({
                                'date': fcst_date,
                                'time': fcst_time,
                                'value': item['fcstValue']
                            })
                            # 오늘 날짜의 TMX 우선 사용
                            if fcst_date == today_str and not tmx_found:
                                result['tmx'] = item['fcstValue']
                                tmx_found = True
                
                # 오늘 날짜에 없으면 내일 날짜에서 찾기
                if not tmn_found and tmn_candidates:
                    # 가장 가까운 날짜의 TMN 사용
                    result['tmn'] = tmn_candidates[0]['value']
                    tmn_found = True
                
                if not tmx_found and tmx_candidates:
                    # 가장 가까운 날짜의 TMX 사용
                    result['tmx'] = tmx_candidates[0]['value']
                    tmx_found = True
                
                # 디버깅 로그 (선택사항)
                if not tmn_found:
                    print(f"[DEBUG] TMN not found. Available candidates: {tmn_candidates}")
                if not tmx_found:
                    print(f"[DEBUG] TMX not found. Available candidates: {tmx_candidates}")
        
        return {
            "status": "success",
            "campus": campus,
            "checkTime": f"{base_date} {base_time[:2]}",
            "forecastTime": f"{forecast_date} {forecast_time[:2]}",
            "data": result
        }

    except Exception as e:
        return {"status": 'error', "message": str(e)}

@app.get("/")
def root():
    """API 서버의 상태를 확인하기 위한 기본 엔드포인트"""
    return {"message": "HUFS Clock API is running."}
