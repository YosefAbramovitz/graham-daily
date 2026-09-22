# סורק גראהם — S&P 1500

סריקה יומית אוטומטית של מדד ה-S&P Composite 1500 מול הקריטריונים של בנג'מין גראהם
למשקיע המגן (פרק 14) ולמשקיע היוזם (פרק 15) מתוך "המשקיע הנבון", כולל מדד פיוטרוסקי
ומספר גראהם עם מרווח הביטחון. עליה רצה שכבת תזמון טכנית לפי קונסטנס בראון.

**הדפים החיים:**

| דף | תוכן |
|---|---|
| `index.html` | המשקיע המגן — שבעת הקריטריונים |
| `enterprising.html` | המשקיע היוזם — חמשת הקריטריונים |
| `technical.html` | השלב הטכני — תזמון על מי שעבר את הסינון הערכי |

## איך זה עובד

| קובץ | תפקיד |
|---|---|
| `graham_screener.py` | מושך נתונים מ-Yahoo Finance ומחשב את הקריטריונים לכל מניה ביקום |
| `build_page.py` | הופך את `results.csv` לשני הדפים הערכיים |
| `tech_screener.py` | לוקח את עוברי גראהם בלבד ומחשב עליהם שכבה טכנית |
| `build_tech_page.py` | הופך את `tech_results.csv` ל-`docs/technical.html` |
| `.github/workflows/daily.yml` | הסריקה הערכית, בכל יום מסחר ב-21:30 UTC (אחרי נעילת ניו יורק) |
| `.github/workflows/technical.yml` | השלב הטכני, בכל בוקר ב-08:00 שעון ישראל |

שני התהליכים רצים על שרתי GitHub, בלי תלות במחשב כלשהו. אפשר גם להריץ ידנית
מלשונית **Actions**.

### למה שני cron לשלב הטכני

GitHub מריץ cron לפי UTC בלבד, ושעון ישראל זז בין UTC+2 ל-UTC+3. לכן מוגדרות שתי
שעות, 05:00 ו-06:00 UTC, והצעד הראשון בעבודה בודק מה השעה בפועל בישראל וממשיך רק
כשהיא 08:00. כך בדיוק ריצה אחת מתבצעת בכל בוקר, בקיץ ובחורף.

## השכבה הטכנית

מבוססת על Constance M. Brown, *Technical Analysis for the Trading Professional*
(McGraw-Hill, 1999):

* **כללי הטווח של ה-RSI** (פרק 1) — התעלה שבה ה-RSI נע תלויה במגמה. בשוק שורי
  תמיכה של 40 עד 50 והתנגדות של 80 עד 90, בשוק דובי תמיכה של 20 עד 30 והתנגדות של
  55 עד 65. מחליף את 30/70 הקבועים.
* **Positive ו-Negative Reversals** (פרק 8, נוסחאות בנספח D) — תבניות היפוך בין
  ה-RSI למחיר, עם יעד מחיר מחושב.
* **האוסילטור הנגזר** (פרק 14) — RSI מוחלק שלוש פעמים כהיסטוגרמה.

מדד הקומפוזיט מפרק 12 אינו מחושב, מפני שהמחברת בחרה לא לפרסם את הנוסחה בספר.

בנוסף מחושבים ATR באחוזים, ADX ומיקום מול ממוצע נע של 200 יום.

## הרצה מקומית

```bash
pip install -r requirements.txt

# השלב הערכי
python graham_screener.py --universe sp1500 --out results.csv
python build_page.py --in results.csv --out docs/index.html --mode defensive
python build_page.py --in results.csv --out docs/enterprising.html --mode enterprising

# השלב הטכני
python tech_screener.py --in results.csv --out tech_results.csv
python build_tech_page.py --in tech_results.csv --out docs/technical.html
```

לבדיקה מהירה על מניות בודדות:

```bash
python graham_screener.py --tickers KO,JNJ,PG,XOM,IBM
python tech_screener.py --tickers KO,JNJ,PG
```

## הגדרה חד-פעמית של GitHub Pages

Settings → Pages → Source: `Deploy from a branch` → Branch: `main`, תיקייה: `/docs` → Save.

## הבהרה

זהו כלי סינון טכני-כמותי בלבד. הוא אינו ייעוץ השקעות ואינו המלצה לקנות או למכור נייר ערך
כלשהו. הנתונים מגיעים ממקור חינמי ועשויים להיות חלקיים או שגויים.
