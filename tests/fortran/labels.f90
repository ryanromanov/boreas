program labels
  implicit none
  integer :: i, j, k, n, found_i, found_j, total

  ! exit outer: find the first pair with i * j == 12
  found_i = 0
  found_j = 0
  outer: do i = 1, 5
    do j = 1, 5
      if (i * j == 12) then
        found_i = i
        found_j = j
        exit outer
      end if
    end do
  end do outer
  print *, found_i, found_j, i

  ! cycle outer: skip the rest of a row once j passes i
  total = 0
  rows: do i = 1, 4
    do j = 1, 4
      if (j > i) cycle rows
      total = total + i * 10 + j
    end do
  end do rows
  print *, total

  ! do while with a label, an unlabeled inner loop and a one-line if
  n = 0
  scan: do while (n < 100)
    n = n + 7
    do k = 1, 3
      if (n > 40) exit scan
      if (mod(n, 2) == 0) cycle scan
    end do
    print *, n
  end do scan
  print *, n

  ! three deep: exit middle, cycle outer, exit outer
  total = 0
  a: do i = 1, 4
    b: do j = 1, 4
      c: do k = 1, 4
        if (k == 3) cycle b
        if (j == 3) exit b
        if (i == 3) cycle a
        if (i == 4) exit a
        total = total + 100 * i + 10 * j + k
      end do c
    end do b
    print *, i, total
  end do a
  print *, i, j, k, total

  ! a construct name that is a Boreas keyword
  in: do i = 1, 3
    if (i == 2) exit in
  end do in
  print *, i
end program labels
