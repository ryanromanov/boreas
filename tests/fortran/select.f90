! select case: integer ranges, several values, character, logical, default, nesting, exit in a loop.
function grade(score) result(g)
  implicit none
  integer, intent(in) :: score
  character(len=1) :: g
  select case (score)
  case (90:100)
    g = 'A'
  case (80:89)
    g = 'B'
  case (70:79)
    g = 'C'
  case (:69)
    g = 'F'
  case default
    g = '?'
  end select
end function grade

subroutine describe(n)
  implicit none
  integer, intent(in) :: n
  integer, parameter :: limit = 10
  select case (n)
  case (-1, 0)
    print *, n, 'zero-ish'
  case (1, 3, 5, 7, 9)
    print *, n, 'small odd'
  case (limit:)
    print *, n, 'big'
  case default
    print *, n, 'other'
  end select
end subroutine describe

program selecting
  implicit none
  integer :: i, k
  character(len=5) :: word
  logical :: flag
  character(len=1) :: grade
  do i = 1, 5
    select case (i * 20 + 5)
    case (:69)
      print *, i, 'low'
    case (70:)
      print *, i, 'high'
    end select
  end do
  print *, grade(95)
  print *, grade(85)
  print *, grade(75)
  print *, grade(10)
  print *, grade(101)
  do i = -2, 12, 3
    call describe(i)
  end do
  word = 'cat'
  select case (word)
  case ('dog', 'wolf')
    print *, 'canine'
  case ('cat')
    print *, 'feline'
  case default
    print *, 'unknown'
  end select
  flag = .false.
  select case (flag)
  case (.true.)
    print *, 'yes'
  case (.false.)
    print *, 'no'
  end select
  do i = 1, 10
    sel: select case (mod(i, 4))
    case (0)
      cycle
    case (3)
      select case (i)
      case (7)
        print *, 'seven, stopping'
        exit
      case default
        print *, i, 'three mod four'
      end select
    case default
      k = i
    end select sel
    print *, 'after', i
  end do
  print *, k
end program selecting
