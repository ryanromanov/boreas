C     FIXED-FORM SOURCE: LABELS, GOTO, LABELLED DO, PARAMETER.
      PROGRAM LABELS
      INTEGER N, I, J, TOTAL, STEPS, R
      PARAMETER (N = 4, LIMIT = 30)
      CHARACTER*(*) GREET
      PARAMETER (GREET = 'fixed form')
      INTEGER GRID(N,N)
C     A LABELLED DO LOOP, ONE TERMINATOR SHARED BY TWO LOOPS
      TOTAL = 0
      DO 10 I = 1, N
      DO 10 J = 1, N
        GRID(I,J) = I * J
   10 TOTAL = TOTAL + GRID(I,J)
      PRINT *, 'total', TOTAL
C     TEST AT THE TOP, JUMP BACK AT THE BOTTOM
      I = 1
   20 IF (I .GT. LIMIT) GOTO 30
        I = I * 2
        GOTO 20
   30 CONTINUE
      PRINT *, 'doubled to', I
C     LEAVE NESTED LOOPS WITH A FORWARD JUMP
      R = 0
      DO 50 I = 1, N
        DO 40 J = 1, N
          IF (GRID(I,J) .GE. 9) GOTO 60
          R = R + 1
   40   CONTINUE
   50 CONTINUE
   60 PRINT *, 'cells before the first 9 or more:', R
C     TWO JUMPS THAT CROSS: A STATE MACHINE
      STEPS = 0
      I = 27
  100 IF (I .EQ. 1) GOTO 300
      STEPS = STEPS + 1
      IF (MOD(I, 2) .EQ. 0) GOTO 200
      I = 3 * I + 1
      GOTO 100
  200 I = I / 2
      IF (STEPS .LT. 1000) GOTO 100
  300 PRINT *, 'collatz steps for 27:', STEPS
C     COMPUTED GOTO AND ARITHMETIC IF
      DO 90 I = 1, 3
        GOTO (71, 72, 73), I
   71   PRINT *, 'one'
        GOTO 80
   72   PRINT *, 'two'
        GOTO 80
   73   PRINT *, 'three'
   80   IF (I - 2) 81, 82, 83
   81   PRINT *, 'below'
        GOTO 90
   82   PRINT *, 'equal'
        GOTO 90
   83   PRINT *, 'above'
   90 CONTINUE
      PRINT *, GREET, INDEX(GREET, 'form'),
     +         LEN(GREET)
      END
