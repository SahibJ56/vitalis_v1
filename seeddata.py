import sqlite3, datetime

db = sqlite3.connect('C:/vitalis/vitalis.db')

YOUR_ID = '6597342112'

days = [
    ('rice and lentils', 'Dal Rice', 450, 18, 80, 8, 3.2, 0, 1.2, 120),
    ('chicken sandwich', 'Chicken Sandwich', 520, 35, 45, 14, 1.1, 0, 0.8, 80),
    ('eggs and toast', 'Eggs and Toast', 380, 22, 40, 12, 2.1, 40, 1.1, 90),
    ('pasta with sauce', 'Pasta', 600, 20, 90, 10, 1.8, 0, 0.5, 60),
    ('oatmeal and banana', 'Oatmeal', 350, 10, 65, 5, 2.0, 0, 0.3, 80),
    ('pizza 3 slices', 'Pizza', 750, 28, 88, 24, 2.5, 0, 1.0, 200),
    ('yogurt and fruit', 'Yogurt and Fruit', 280, 12, 45, 4, 0.5, 20, 0.9, 300),
]

for i, (raw, clean, cal, pro, carb, fat, iron, vitd, b12, calc) in enumerate(days):
    dt = (datetime.datetime.now() - datetime.timedelta(days=6-i)).isoformat()
    db.execute(
        '''INSERT INTO food_logs (user_id, username, food_raw, food_clean, calories, protein, carbs, fat, iron, vitamin_d, vitamin_b12, calcium, health_score, logged_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
        (YOUR_ID, 'demo', raw, clean, cal, pro, carb, fat, iron, vitd, b12, calc, 6, dt)
    )

db.commit()
db.close()
print('Done — 7 days of meals loaded!')