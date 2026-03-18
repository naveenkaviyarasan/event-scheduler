"""
Run this script to completely reset the database.
Use this if you see any login errors or database errors.

Usage:
    python reset_db.py
"""
import os
from app import app, db, seed

DB_PATH = os.path.join(os.path.dirname(__file__), 'instance', 'scheduler.db')
ALT_PATH = os.path.join(os.path.dirname(__file__), 'scheduler.db')

# Delete old DB file if it exists
for path in [DB_PATH, ALT_PATH]:
    if os.path.exists(path):
        os.remove(path)
        print(f'Deleted: {path}')

with app.app_context():
    db.drop_all()
    db.create_all()
    seed()
    print('✅ Database reset complete!')
    print('   Login: admin / admin123')
